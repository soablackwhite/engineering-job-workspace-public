"""Exercise native browser on a loopback-only synthetic form, never real candidate data."""
import json,os,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from workflow.tests.test_pilot import PilotTests
from workflow.browser_driver import AgentBrowser
from workflow.pilot import Pilot

class Form(BaseHTTPRequestHandler):
 submissions=0
 def log_message(self,*args):pass
 def do_GET(self):
  body=b'''<html><body><h1>LOCAL SYNTHETIC APPLICATION</h1><form action="/complete" method="post" enctype="multipart/form-data"><label id="name-label" for="name">Full name</label><input name="name" id="name"><input type="file" name="resume" id="resume"><button id="submit" type="submit">Submit LOCAL fixture</button></form></body></html>'''
  self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body)
 def do_POST(self):
  data=self.rfile.read(int(self.headers.get('Content-Length','0')))
  if b'SYNTHETIC CANDIDATE' not in data or b'fixture-only.pdf' not in data:raise AssertionError('Unexpected fixture payload')
  Form.submissions+=1
  self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(b'<p id="message">LOCAL fixture received</p><p id="job">Synthetic job</p><p id="reference">FIXTURE-001</p>')

def run():
 case=PilotTests();case.setUp();server=ThreadingHTTPServer(('127.0.0.1',0),Form);threading.Thread(target=server.serve_forever,daemon=True).start()
 browser=AgentBrowser('job-fixture-'+str(int(time.time())),case.path/'browser')
 try:
  origin='http://127.0.0.1:'+str(server.server_port);case.grant['url']=origin+'/form';case.grant['confirmation']['url']=origin+'/complete';case.bind_grant()
  pilot=Pilot(case.p,browser,fixture=True);aid=pilot.prepare('synthetic',case.token,case.grant,case.pdf)
  pilot.open('synthetic',aid);pilot.fill('synthetic',aid,'name');pilot.upload('synthetic',aid);review=pilot.review('synthetic',aid)
  pilot.submit('synthetic',aid,{'approved_by':'user','review_sha256':review['review_sha256'],'expires_at':time.time()+60});confirmation=pilot.confirm('synthetic',aid)
  assert Form.submissions==1
  report={'fixture_only':True,'candidate_data_used':False,'native_browser':True,'local_posts':Form.submissions,'external_submissions':0,'final_state':'confirmed','confirmation':confirmation,'attempt_id':aid}
  print(json.dumps(report,indent=2));return report
 finally:browser.close_fixture();server.shutdown();case.tearDown()
if __name__=='__main__':
 import sys
 report=run()
 if len(sys.argv)>1:Path(sys.argv[1]).write_text(json.dumps(report,indent=2)+'\n')
