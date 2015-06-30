#!/usr/bin/env python
#
"""pipe2py for Google App Engine

   Run Yahoo pipes on App Engine

   Author: Greg Gaughan
           with modules added by: Tony Hirst
   Idea: Tony Hirst (http://ouseful.wordpress.com/2010/02/25/starting-to-think-about-a-yahoo-pipes-code-generator)
   Python generator pipelines inspired by: David Beazely (http://www.dabeaz.com/generators-uk)
   Universal Feed Parser module by: Mark Pilgrim (http://feedparser.org)
"""

import sys
import os
import imp
import logging
import datetime
import time

from google.appengine.ext import webapp
from google.appengine.ext.webapp import util

from google.appengine.ext import db
from google.appengine.api import users

from google.appengine.runtime import apiproxy_errors

from google.appengine.api.urlfetch import DownloadError

from pipe2py import Context
import pipe2py.compile

import json
import urllib

PAGESIZE = 15

class Pipe(db.Model):
    """Stores pipe details in the datastore"""
    author = db.UserProperty()
    pipe_id = db.StringProperty()
    title = db.StringProperty()
    json = db.TextProperty()
    python = db.TextProperty()
    created = db.DateTimeProperty(auto_now_add=True)
    updated = db.DateTimeProperty(auto_now=True)

class DatastoreImporter(object):
    """Intercepts pipe module imports and loads them from the datastore
       - this will be used for dynamically loading pipe definitions as 
         well as those pipes importing sub-pipes
    """
    PATH_TRIGGER = "DatastoreImporter_PATH_TRIGGER"
    
    def __init__(self, path_entry):
        self.path_entry = path_entry
        if path_entry != self.PATH_TRIGGER:
            raise ImportError()
    
    def find_module(self, module_name, package_path=None):
        if package_path:
            return None
        if not module_name.startswith('pipe_'):
            return None
        return self
   
    def load_module(self, module_name):
        """Load source from datastore"""
        pipe_id = module_name[5:]
        logging.debug("Loading module from datastore: %s" % (module_name))
        pipes = db.GqlQuery("SELECT * FROM Pipe WHERE pipe_id = :1", pipe_id)
        #todo filter by user
        pipe = pipes.get()
        if not pipe:
            raise ImportError("No pipe module found for %s" % (module_name))
        we_added = False
        if module_name in sys.modules:
            mod = sys.modules[module_name]
        else:
            we_added = True
            mod = sys.modules.setdefault(module_name, imp.new_module(module_name))
        mod.__file__ = '<%s [%s]>' % (self.__class__.__name__, module_name)
        mod.__path__ = self.path_entry
        mod.__loader__ = self
        mod.__package__ = ''
        if not pipe.python:
            #todo perhaps return empty module instead?
            raise ImportError("Module was empty for %s (%s)" % (module_name, e))
        source = pipe.python.replace('\r\n', '\n') #todo find out who put the \r in!? GAE?
        try:
            exec source in mod.__dict__
        except Exception, e:
            if we_added:
                del sys.modules[module_name]
            raise ImportError("Module failed to parse for %s (%s)" % (module_name, e))
        return mod
        
    
def get_pipe(pipe_id):
    url = ("""http://query.yahooapis.com/v1/public/yql"""
           """?q=select%20PIPE%20from%20json%20"""
           """where%20url%3D%22http%3A%2F%2Fpipes.yahoo.com%2Fpipes%2Fpipe.info%3F_out%3Djson%26_id%3D"""
               + pipe_id + 
               """%22&format=json""")
    try:
        pjson = urllib.urlopen(url).readlines()
    except DownloadError, e:
        logging.error("Error contacting Yahoo for %s - %s (try again later)" % (pipe_id, e))
        return (None, None)
        
    pjson = "".join(pjson)
    pipe = json.loads(pjson)
    if not pipe['query']['results']:
        logging.warn("Pipe not found on Yahoo %s" % pipe_id)
        return (None, None)
    pipe_name = pipe['query']['results']['json']['PIPE']['name']
    pipe_json = json.dumps(pipe['query']['results']['json']['PIPE']['working']) #was not needed until April 2011 - changes at Yahoo! Pipes/YQL?

    return (pipe_name, pipe_json)
        

  
class MainHandler(webapp.RequestHandler):
      
    def get(self):
        self.response.out.write('<html><head><title>pipes engine</title>')
        self.response.out.write('<link type="text/css" rel="stylesheet" href="/css/main.css" />')
        self.response.out.write('''<script type="text/javascript" src="https://apis.google.com/js/plusone.js">''')
        self.response.out.write('''    {lang: 'en-GB'}''')
        self.response.out.write('''</script>''')
        self.response.out.write('</head>')
        self.response.out.write("""<body><div class='header'>
        <h1>pipes engine</h1>""")
        if users.get_current_user():
            self.response.out.write("""<p class="user"><g:plusone></g:plusone> <a href="%s">Logout</a></p>""" % users.create_logout_url("/"))
        else:
            self.response.out.write("""<p class="user"><g:plusone></g:plusone> <a href="%s">Login</a></p>""" % users.create_login_url("/"))
        self.response.out.write("""<p class="tagline">Yahoo! Pipes compiled by pipe2py <a href="/about">(about)</a></p>
        </div>
        <div class="content">""")
        
        #Pagination
        next = None
        bookmark = self.request.get("page")
        if bookmark:
            try:
                bookmark = int(bookmark)
                if bookmark < 1:
                    bookmark = 0
            except:
                bookmark = 0
        else:
            bookmark = 0
    
        #pipes = db.GqlQuery("SELECT * "
                            #"FROM Pipe "
                            #"ORDER BY updated DESC "
                            ##"DESC LIMIT 10"
                           #)
                           
        pipes = db.Query(Pipe).order('-updated').fetch(PAGESIZE+1, bookmark*PAGESIZE)
        #todo filter by user
        
        if len(pipes) == PAGESIZE+1:
            next = bookmark + 1
            pipes = pipes[:PAGESIZE]
        
        self.response.out.write('<form action="/update" method="post">')
        self.response.out.write('<table cellspacing="0">')
        self.response.out.write('<thead>')
        self.response.out.write('<tr class="head">')
        self.response.out.write('<th>Author</th>')
        self.response.out.write('<th>Pipe Id</th>')
        self.response.out.write('<th>Title</th>')
        self.response.out.write('<th>Updated</th>')
        self.response.out.write('<th></th>')
        self.response.out.write('</tr>')
        self.response.out.write('</thead>')
        for pipe in pipes:
            self.response.out.write('<tr>')
            if pipe.author:
                self.response.out.write('<td>%s</td>' % pipe.author.nickname())
            else:
                self.response.out.write('<td>anonymous</td>')
            self.response.out.write('<td><a href="info/%(pipe_id)s">%(pipe_id)s</a></td>' % {'pipe_id':pipe.pipe_id})
            self.response.out.write('<td>%s</td>' % pipe.title)
            self.response.out.write('<td>%s</td>' % pipe.updated.strftime('%c'))
            self.response.out.write("""<td align="center"><input type="checkbox" name="%s" value="selected" /></td>""" % pipe.pipe_id)
            self.response.out.write('</tr>')
        self.response.out.write('<tr><td/><td/><td/><td/><td align="center"><input type="submit" name="reload" value="Reload" /></td></tr>')
        self.response.out.write('<tr><td/><td/><td/><td/><td align="center"><input type="submit" name="delete" value="Delete" /></td></tr>')
        if bookmark or next:
            self.response.out.write('<tr><td/><td/><td/><td align="center">')
            if bookmark==1:
                self.response.out.write('<a class="nav" href="/">< Previous %s</a> ' % PAGESIZE)
            elif bookmark:
                self.response.out.write('<a class="nav" href="/?page=%s">< Previous %s</a> ' % (str(bookmark-1), PAGESIZE))
            if next:
                self.response.out.write('<a class="nav" href="/?page=%s">Next %s ></a>' % (next, PAGESIZE))
            self.response.out.write('</td><td/></tr>')
        self.response.out.write('</table>')
        self.response.out.write("""</form>""")
            
        
        #self.response.out.write('<br />')        
        self.response.out.write("""
              <div class="footer"><form action="/add" method="post">
                Pipe Id: <input type="text" name="pipe_id" size="36" />
                     <input type="submit" value="Add pipe" />
                     <img class="powered_by" src="http://code.google.com/appengine/images/appengine-noborder-120x30.gif" alt="Powered by Google App Engine" />
              </form></div>""")
        #self.response.out.write('<img class="powered_by" src="http://code.google.com/appengine/images/appengine-noborder-120x30.gif" alt="Powered by Google App Engine" />')
        
        self.response.out.write('</div>')
        self.response.out.write('</body></html>')
      
class PipeAdd(webapp.RequestHandler):
    """
    
       #todo also add (or prompt at least) any dependency pipes
    """
    def post(self):
        #if True: #todo users.is_current_user_admin():
        if users.get_current_user():
            #todo restrict anonymous adding?
            pipe_id = self.request.get('pipe_id').strip()
            
            pipes = db.GqlQuery("SELECT * FROM Pipe WHERE pipe_id = :1", pipe_id)
            #todo filter by user
            pipe = pipes.get()
            if pipe:
                #Pipe already exists - so just reload it (which will bring it back to the front page)
                #todo refactor this to share the explicit reload code!
                logging.debug("Updating pipe (instead of re-adding it) %s" % pipe_id)
                pipe_name, pipe_json = get_pipe(pipe_id)
                if pipe_json:
                    #compile the pipe
                    context = Context()
                    try:
                        pipe_def = json.loads(pipe_json)
                        source = pipe2py.compile.parse_and_write_pipe(context, pipe_def, ("pipe_%s" % pipe_id))
                    except:
                        logging.error("Failed to compile pipe %s" % pipe_id)
                        #todo add to error_messages
                        self.response.out.write('Failed to compile pipe %s' % pipe_id)
                        return
                    
                    #store the pipe
                    pipe.title = pipe_name
                    pipe.json = pipe_json
                    pipe.python = source
    
                    try:
                        pipe.put()
                    except CapabilityDisabledError:
                        # todo: fail gracefully here
                        logging.error("Failed to store updated pipe %s" % pipe_id)
                        self.response.out.write('Failed to store updated pipe %s' % pipe_id)
                        return
                else:
                    #todo add to error_messages
                    self.response.out.write('Failed re-loading pipe definition from Yahoo')
                    return
            else:
                #Add a new one
                pipe_name, pipe_json = get_pipe(pipe_id)
    
                if pipe_json:
                    #compile the pipe
                    context = Context()
                    try:
                        pipe_def = json.loads(pipe_json)
                        source = pipe2py.compile.parse_and_write_pipe(context, pipe_def, ("pipe_%s" % pipe_id))
                    except:
                        logging.error("Failed to compile pipe %s" % pipe_id)
                        raise  #todo print error and handle gracefully instead!
                    
                    #store the pipe
                    pipe = Pipe()
                    pipe.pipe_id = pipe_id
                    pipe.title = pipe_name
                    pipe.json = pipe_json
                    pipe.python = source
                    pipe.author = users.get_current_user()
    
                    try:
                        pipe.put()
                    except CapabilityDisabledError:
                        # todo: fail gracefully here
                        raise
                else:
                    self.response.out.write('Failed loading pipe definition from Yahoo')
                    return
        else:
            self.redirect(users.create_login_url("/"))
            return
        
        self.redirect('/')
      
class PipeUpdate(webapp.RequestHandler):
    """Reload or delete pipe(s)
    """
    def post(self):
        if 'reload' in self.request.params:
            #if True: #todo users.is_current_user_admin():
            if users.get_current_user():
                #todo restrict anonymous updating?
                #todo at least only allow owner!
                for pipe_id in self.request.params:
                    pipes = db.GqlQuery("SELECT * FROM Pipe WHERE pipe_id = :1", pipe_id)
                    #todo filter by user
                    pipe = pipes.get()
                    if pipe:
                        logging.debug("Updating pipe %s" % pipe_id)
                        pipe_name, pipe_json = get_pipe(pipe_id)
                        if pipe_json:
                            #compile the pipe
                            context = Context()
                            try:
                                pipe_def = json.loads(pipe_json)
                                source = pipe2py.compile.parse_and_write_pipe(context, pipe_def, ("pipe_%s" % pipe_id))
                            except:
                                logging.error("Failed to compile pipe %s" % pipe_id)
                                #todo add to error_messages
                                continue  #try next one
                            
                            #store the pipe
                            pipe.title = pipe_name
                            pipe.json = pipe_json
                            pipe.python = source
            
                            try:
                                pipe.put()
                            except CapabilityDisabledError:
                                # todo: fail gracefully here
                                logging.error("Failed to store pipe %s" % pipe_id)
                                #todo add to error_messages
                                continue  #try next one
                        else:
                            #todo add to error_messages
                            pass  #update date won't change
            else:
                self.redirect(users.create_login_url("/"))
                return
                        
        elif 'delete' in self.request.params:
            #if True: #todo users.is_current_user_admin():
            if users.get_current_user():
                #todo restrict anonymous deleting!
                #todo at least only allow owner!
                for pipe_id in self.request.params:
                    pipes = db.GqlQuery("SELECT * FROM Pipe WHERE pipe_id = :1", pipe_id)
                    #todo filter by user
                    pipe = pipes.get()
                    if pipe:
                        logging.debug("Deleting pipe %s" % pipe_id)
                        try:
                            pipe.delete()
                            pipe_name = "pipe_%s" % pipe_id
                            #Note: the pipe code will remain in the module cache
                        except CapabilityDisabledError:
                            # todo: fail gracefully here
                            logging.error("Failed to delete pipe %s" % pipe_id)
                            #todo add to error_messages
                            continue  #try next one
                    #todo else: error loading - add to error_messages
            else:
                self.redirect(users.create_login_url("/"))
                return
                    
        #todo else: 404 error!
        
        self.redirect('/')

class PipeInfo(webapp.RequestHandler):      
    def get(self, pipe_id):
        if True: #todo users.is_current_user_admin(): #or pipe owner
            pipe_name = "pipe_%s" % pipe_id
            #todo prevent injection
            pipes = db.GqlQuery("SELECT * FROM Pipe WHERE pipe_id = :1", pipe_id)
            #todo filter by user
            pipe = pipes.get()
            if pipe:
                #We need to ensure our module loader is always available (GAE seems to keep removing it)
                sys.meta_path.append(DatastoreImporter(DatastoreImporter.PATH_TRIGGER))  #needed to allow nested imports
                try:
                    if pipe_name in sys.modules:
                        #for now, we always reload the module code, in case it's changed
                        #todo: when a pipe is updated, we should remove it from sys.modules then this will never happen
                        reload(sys.modules[pipe_name])  #todo: there is a case for caching if it's not changed since last time
                        pm = sys.modules[pipe_name]
                    else:
                        pm = __import__(pipe_name)
                except apiproxy_errors.OverQuotaError, e:
                    logging.error(e)
                    self.response.out.write('Failed loading %s (the quota has been exceeded when loading - please try again later)' % pipe_name)
                    return
                except Exception, e:
                    logging.error(e)
                    #Note: e "Module not found" hides the original error (if it was a pipe2py error at least)
                    self.response.out.write("Failed loading %s (%s)" % (pipe_name, "ensure any embedded pipes have been added"))
                    return
                #Find out which input parameters we need
                context = Context(describe_input=True)
                need_inputs = pm.__dict__[pipe_name](context, None)

                if not need_inputs:
                    self.redirect("/run/%s" % pipe_id)
                
                inputs = dict([(i[1], self.request.get(i[1], i[4])) for i in need_inputs])  #use any query parameters as inputs
                #todo inputs = dict([(i[1], self.request.get(i[1], None)) for i in need_inputs])  #use any query parameters as inputs
                #todo HERE: any inputs that are still None need to either use the default or use web-form
                #todo present web-form for any missing inputs
                # and run POSTs to PipeRun
                self.response.out.write('<html><head><title>pipes engine</title>')
                self.response.out.write('<link type="text/css" rel="stylesheet" href="/css/main.css" /></head>')
                self.response.out.write("""<body><div class='header'>
                <h1>pipes engine</h1>""")
                if users.get_current_user():
                    self.response.out.write("""<p class="user"><a href="%s">Logout</a></p>""" % users.create_logout_url("/"))
                else:
                    self.response.out.write("""<p class="user"><a href="%s">Login</a></p>""" % users.create_login_url("/"))
                self.response.out.write("""<p class="tagline">Yahoo! Pipes compiled by pipe2py <a href="/about">(about)</a></p>
                </div>
                <div class="content">""")
                
                self.response.out.write('<a href="/" class="nav">< Back to pipes</a>')
                self.response.out.write('<h2>%s</h2>' % pipe.title)
                self.response.out.write('<p>%s</p>' % pipe_id)
                self.response.out.write('<form action="/run/%s" method="post">' % pipe_id)
                self.response.out.write('<table class="configure" cellspacing="0">')
                self.response.out.write('<thead>')
                self.response.out.write('<tr class="head">')
                self.response.out.write('<th>Configure the pipe</th>')
                self.response.out.write('<th></th>')
                self.response.out.write('</tr>')
                self.response.out.write('</thead>')
                
                for i in need_inputs:
                    self.response.out.write('<tr>')
                    self.response.out.write('<td>%s</td>' % i[2])
                    self.response.out.write('<td><input type="text" name="%s" size="36" value="%s"/></td>' % (i[1], inputs[i[1]]))
                    self.response.out.write('</tr>')
                    #todo add javascript to validate based on type i[3]
                self.response.out.write('</table>')
                    
                #self.response.out.write('<br />')        
                self.response.out.write("""
                      <div class="footer">
                             <input type="submit" name="run" value="Run Pipe" />
                             <img class="powered_by" src="http://code.google.com/appengine/images/appengine-noborder-120x30.gif" alt="Powered by Google App Engine" />                
                      </form></div>""")
                self.response.out.write("""</form>""")
                #self.response.out.write('<img class="powered_by" src="http://code.google.com/appengine/images/appengine-noborder-120x30.gif" alt="Powered by Google App Engine" />')
                
                self.response.out.write('</div>')
                self.response.out.write('</body></html>')
            else:
                self.response.out.write("Unknown pipe " + pipe_id)
        else:
            self.response.out.write("Not privileged to run " + pipe_id)

class PipesEncoder(json.JSONEncoder): 
    """Extends JSONEncoder to add support for date and time properties. 
    """ 
    def default(self, obj): 
        """Tests the input object, obj, to encode as JSON.""" 
        if hasattr(obj, '__json__'): 
            return getattr(obj, '__json__')() 

        if isinstance(obj, datetime.datetime): 
            output = obj.strftime("%Y-%m-%dT%H:%M:%SZ")
            return output   
        elif isinstance(obj, time.struct_time): 
            dt = datetime.datetime.fromtimestamp(time.mktime(obj))
            output = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            return output
  
        return json.JSONEncoder.default(self, obj) 

class PipeRun(webapp.RequestHandler):      
    def post(self, pipe_id):
        self.get(pipe_id)
        
    def get(self, pipe_id):
        if True: #todo users.is_current_user_admin(): #or pipe owner
            #Load request args into context inputs
            inputs = dict([(a, self.request.get(a, None)) for a in self.request.arguments() if a != 'run'])
            context = Context(console=False, inputs=inputs)

            pipe_name = "pipe_%s" % pipe_id
            #todo prevent injection
            pipes = db.GqlQuery("SELECT * FROM Pipe WHERE pipe_id = :1", pipe_id)
            #todo filter by user
            pipe = pipes.get()
            if pipe:
                #We need to ensure our module loader is always available (GAE seems to keep removing it)
                sys.meta_path.append(DatastoreImporter(DatastoreImporter.PATH_TRIGGER))  #needed to allow nested imports
                try:
                    if pipe_name in sys.modules:
                        #for now, we always reload the module code, in case it's changed
                        #todo: when a pipe is updated, we should remove it from sys.modules then this will never happen
                        reload(sys.modules[pipe_name])  #todo: there is a case for caching if it's not changed since last time
                        pm = sys.modules[pipe_name]
                    else:
                        pm = __import__(pipe_name)
                except apiproxy_errors.OverQuotaError, e:
                    logging.error(e)
                    self.response.out.write('Failed loading %s (the quota has been exceeded when loading - please try again later)' % pipe_name)
                    return
                except Exception, e:
                    logging.error(e)
                    #Note: e "Module not found" hides the original error (if it was a pipe2py error at least)
                    self.response.out.write("Failed loading %s (%s)" % (pipe_name, "ensure any embedded pipes have been added"))
                    return
                
                p = pm.__dict__[pipe_name](context, None)
                
                self.response.headers["Content-Type"] = "application/json"
                #todo or we could build and run from the json instead, i.e.
                #pipe_def = json.loads(pipe.json)
                #p = pipe2py.compile.parse_and_build_pipe(context, pipe_def)
                
                #Output header (for json) - perhaps push this into the output module - or an app-engine output wrapper module
                self.response.out.write("""{"value":{"title":"%(title)s",
                "description":"Pipes Output",
                "link":"http:\/\/pipes-engine.appspot.com\/info\/%(id)s",
                "generator":"http:\/\/pipes-engine.appspot.com",
                "items":[
                """ % {'title':pipe.title, 'id': pipe_id})
                #todo add: "pubDate":"Fri, 03 Dec 2010 20:46:30 +0000",
                #todo add: "callback":"",
    
                #Output results
                count = 0
                try:
                    for i in p:
                        si = json.dumps(i, cls=PipesEncoder)
                        if count:
                            self.response.out.write(",")
                        self.response.out.write(si)
                        count += 1
                except Exception, e:
                    logging.error(e)
                    self.response.out.write("Error running: %s : " % pipe_id)
                    self.response.out.write(e)
                    #todo print trace?
                    
                #Output footer
                self.response.out.write("""]}, "count":%(count)s}""" % {'count':count})
            else:
                self.response.out.write("Unknown pipe " + pipe_id)
        else:
            self.response.out.write("Not privileged to run " + pipe_id)

            
class PipeAbout(webapp.RequestHandler):
      
    def get(self):
        self.response.out.write('<html><head><title>pipes engine</title>')
        self.response.out.write('<link type="text/css" rel="stylesheet" href="/css/main.css" /></head>')
        self.response.out.write("""<body><div class='header'>
        <h1>pipes engine</h1>""")
        if users.get_current_user():
            self.response.out.write("""<p class="user"><a href="%s">Logout</a></p>""" % users.create_logout_url("/"))
        else:
            self.response.out.write("""<p class="user"><a href="%s">Login</a></p>""" % users.create_login_url("/"))
        self.response.out.write("""<p class="tagline">Yahoo! Pipes compiled by pipe2py <a href="/about">(about)</a></p>
        </div>
        <div class="content">""")
        
        self.response.out.write('<a href="/" class="nav">< Back to pipes</a>')
        self.response.out.write('<h2>About</h2>')
        self.response.out.write("<p>pipes engine retrieves and stores Yahoo! Pipes definitions, compiles them to Python and then runs them when requested on Google's App Engine.</p>")
        self.response.out.write('<br />')
        self.response.out.write('<p>The compilation is done by the open source <a href="http://ggaughan.github.com/pipe2py/">pipe2py</a> compiler.</p>')
        self.response.out.write('<p>(Not all of the Yahoo! Pipes modules have been written yet. If your pipe gives a "...is not defined" error, and you know Python, then please help.)</p>')
        self.response.out.write('<br />')
        self.response.out.write('<p>Developed by <a href="http://www.wordloosed.com">Greg Gaughan</a> from an idea by <a href="http://ouseful.wordpress.com">Tony Hirst</a>.</p>')
        self.response.out.write('<br />')
        self.response.out.write('<p>For more information, visit <a href="http://www.wordloosed.com/running-yahoo-pipes-on-google-app-engine">this blog post</a>.</p>')
        self.response.out.write('<br />')
        self.response.out.write('<table cellspacing="0" class="configure">')
        self.response.out.write('<thead>')
        self.response.out.write('<tr class="head">')
        self.response.out.write('<th>Component</th>')
        self.response.out.write('<th>Version</th>')
        self.response.out.write('</tr>')
        self.response.out.write('</thead>')
        self.response.out.write('<tr><td>pipes engine</td><td>%s</td></tr>' % os.environ.get('CURRENT_VERSION_ID', '?'))
        self.response.out.write('<tr><td>pipe2py</td><td>%s</td></tr>' % pipe2py.compile.__version__)
        self.response.out.write('<tr><td>App Engine</td><td>%s</td></tr>' % os.environ.get('SERVER_SOFTWARE', '?'))
        self.response.out.write('</table>')

        self.response.out.write('<br />')        
        
        self.response.out.write("""
              <div class="footer">
                     <img class="powered_by" src="http://code.google.com/appengine/images/appengine-noborder-120x30.gif" alt="Powered by Google App Engine" />                
              </form></div>""")
        #self.response.out.write('<img class="powered_by" src="http://code.google.com/appengine/images/appengine-noborder-120x30.gif" alt="Powered by Google App Engine" />')
        
        self.response.out.write('</div>')
        self.response.out.write('</body></html>')
            

def main():
    application = webapp.WSGIApplication([('/', MainHandler),
                                          ('/add', PipeAdd),
                                          ('/about', PipeAbout),
                                          ('/update', PipeUpdate),
                                          ('/info/(?P<pipe_id>\w+)', PipeInfo),
                                          ('/run/(?P<pipe_id>\w+)', PipeRun),
                                         ],
                                         debug=True)
    util.run_wsgi_app(application)


if __name__ == '__main__':
    #sys.path_hooks.append(DatastoreImporter)
    sys.meta_path.append(DatastoreImporter(DatastoreImporter.PATH_TRIGGER))
    sys.path.insert(0, DatastoreImporter.PATH_TRIGGER)
    main()

