import sys, os, threading, time, urllib.request, json, socketserver
sys.path.insert(0, '.')
from app import FileAnalyserHandler

server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), FileAnalyserHandler)
port = server.server_address[1]
t = threading.Thread(target=server.serve_forever, daemon=True)
t.start()
time.sleep(0.3)

base = os.getcwd().replace(os.sep, '/')

# 1. initial scan
r = urllib.request.urlopen('http://127.0.0.1:%d/api/scan?path=%s&depth=2' % (port, base), timeout=15)
d1 = json.loads(r.read())
print('1: %s, %d files' % (d1['scan_info']['total_size_formatted'], d1['scan_info']['total_files']))

# 2. scan a subfolder that exists
subdir = os.path.join(os.getcwd(), 'output').replace(os.sep, '/')
r = urllib.request.urlopen('http://127.0.0.1:%d/api/scan_dir?path=%s' % (port, subdir), timeout=15)
d2 = json.loads(r.read())
print('2: %s, %d files' % (d2['scan_info']['total_size_formatted'], d2['scan_info']['total_files']))

# 3. scan another
subdir2 = os.path.join(os.getcwd(), '__pycache__').replace(os.sep, '/')
r = urllib.request.urlopen('http://127.0.0.1:%d/api/scan_dir?path=%s' % (port, subdir2), timeout=15)
d3 = json.loads(r.read())
print('3: %s, %d files' % (d3['scan_info']['total_size_formatted'], d3['scan_info']['total_files']))

# 4. cancel + rescan
r = urllib.request.urlopen('http://127.0.0.1:%d/api/cancel_scan' % port)
print('4: cancel=%s' % json.loads(r.read())['status'])

r = urllib.request.urlopen('http://127.0.0.1:%d/api/scan_dir?path=%s' % (port, subdir), timeout=15)
d4 = json.loads(r.read())
print('5: %s, %d files' % (d4['scan_info']['total_size_formatted'], d4['scan_info']['total_files']))

server.shutdown()
print('ALL OK')
