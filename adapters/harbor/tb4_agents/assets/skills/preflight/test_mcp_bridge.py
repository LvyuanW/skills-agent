import subprocess,sys,os,time,json,socket
from pathlib import Path
python=sys.executable
bridge=str(Path(__file__).resolve().parents[1] / "mcp_bridge.py")
root=Path(os.environ.get("TB4_PREFLIGHT_EVIDENCE_DIR","/data/tb4/artifacts/skills-native-preflight"));root.mkdir(parents=True,exist_ok=True)
config=root/"mcp-config.json";sock=str(root/"mcp.sock")
config.write_text(json.dumps([{"name":"fixture","transport":"sse","url":"http://127.0.0.1:18763/sse"}]))
server=subprocess.Popen([python,str(Path(__file__).with_name("test_mcp_fixture.py"))],stdout=(root/"server.log").open("w"),stderr=subprocess.STDOUT)
client=None
try:
 for _ in range(100):
  try:
   with socket.create_connection(("127.0.0.1",18763),timeout=.1):break
  except OSError:time.sleep(.1)
 client=subprocess.Popen([python,bridge,"--socket",sock,"serve","--config",str(config)],stdout=(root/"bridge.log").open("w"),stderr=subprocess.STDOUT)
 for _ in range(100):
  if Path(sock).exists():break
  if client.poll() is not None:raise RuntimeError("Bridge exited")
  time.sleep(.1)
 outputs=[]
 for command in (["list","fixture"],["call","fixture","counter","{}"],["call","fixture","counter","{}"]):
  result=subprocess.run([python,bridge,"--socket",sock,*command],capture_output=True,text=True,check=True)
  outputs.append(json.loads(result.stdout))
 assert outputs[1]["content"][0]["text"]=="1"
 assert outputs[2]["content"][0]["text"]=="2"
 (root/"mcp-result.json").write_text(json.dumps({"transport":"sse","tool_discovery":len(outputs[0]["tools"]),"state_persisted":True,"results":outputs},indent=2)+"\n")
 print("SSE MCP discovery/call/persistent-state passed")
finally:
 if client:
  client.terminate();client.wait(timeout=10)
 server.terminate();server.wait(timeout=10)
