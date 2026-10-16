from pathlib import Path
import base64,io,json,os,tempfile,sys
import subprocess
import http.client
import ssl
from types import SimpleNamespace
root=Path(os.environ.get("TB4_PREFLIGHT_EVIDENCE_DIR","/data/tb4/artifacts/skills-native-preflight"));root.mkdir(parents=True,exist_ok=True)
from skills_agent.feedback import feedback_content
from skills_agent.session import Session
from skills_agent.providers.deepseek import DeepSeekCounter
from skills_agent.providers.openai import OpenAIProvider
from skills_agent.providers import base
import importlib.util
assets=Path(__file__).resolve().parents[1]
runtime_assets=Path(json.loads((assets/"agent.json").read_text())["kwargs"]["assets_path"])
tokenizer=str(runtime_assets/"tokenizer-v41.json")
spec=importlib.util.spec_from_file_location("tb4_audit",assets/"sitecustomize.py")
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
log=root/"audit-offline.jsonl";log.unlink(missing_ok=True)
os.environ["TB4_API_AUDIT_LOG"]=str(log)
requests=[]
usage={"prompt_tokens":123,"prompt_cache_hit_tokens":20,"completion_tokens":42,"total_tokens":165,"completion_tokens_details":{"reasoning_tokens":30}}
def fake(url,payload,headers):
 requests.append(payload)
 event={"id":"synthetic-not-a-provider-response","model":"deepseek-v4.1-flash","usage":usage}
 events=[event,event,None]
 return base.DiagnosticStream((event for event in events),SimpleNamespace())
base.post_stream=fake
audit._install()
os.environ["TB4_ALLOWED_API_BASE"]="https://yuzhiapi.xyz/v1"
os.environ["effort"]="max"
gateway_model=OpenAIProvider("deepseek-v4.1-flash","TEST_NOT_REAL",base_url="https://yuzhiapi.xyz/v1",token_counter="deepseek",tokenizer_path=tokenizer,tokenizer_encoding="v41",deepseek_effort="max")
gateway_payload=gateway_model._chat_payload("",[{"role":"user","content":"offline fixture"}],[])
assert gateway_payload["reasoning_effort"]=="max"
gateway_aux=gateway_model._chat_payload("",[{"role":"user","content":"offline fixture"}],[],disable_thinking=True)
assert gateway_aux["thinking"]["type"]=="disabled" and "reasoning_effort" not in gateway_aux
assert gateway_model.count_tokens("",[{"role":"user","content":"offline fixture"}],[])>0
os.environ["TB4_ALLOWED_API_BASE"]="https://api.deepseek.com"
stream=base.post_stream("https://api.deepseek.com/chat/completions",{"model":"deepseek-v4.1-flash","reasoning_effort":"max","max_tokens":32768,"messages":[{"role":"assistant","content":None}],"tools":[{"function":{"name":"shell"}}]}, {"Authorization":"Bearer SENTINEL_MUST_NOT_BE_LOGGED"})
list(stream)
assert "SENTINEL" not in log.read_text()
for effort in ("low",None):
 try:base.post_stream("https://api.deepseek.com/chat/completions",{"model":"deepseek-v4.1-flash","reasoning_effort":effort}, {})
 except RuntimeError:pass
 else:raise AssertionError("Effort guard failed")
aux=base.post_stream("https://api.deepseek.com/chat/completions",{"model":"deepseek-v4.1-flash","thinking":{"type":"disabled"},"max_tokens":8192},{});list(aux)
closed=base.post_stream("https://api.deepseek.com/chat/completions",{"model":"deepseek-v4.1-flash","reasoning_effort":"max","max_tokens":32768},{})
next(closed);closed.close()
with tempfile.TemporaryDirectory(dir=root) as temp:
 import struct,zlib
 def chunk(t,d):return struct.pack("!I",len(d))+t+d+struct.pack("!I",zlib.crc32(t+d)&0xffffffff)
 p=Path(temp)/"test.png";p.write_bytes(b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack("!2I5B",16,16,8,2,0,0,0))+chunk(b"IDAT",zlib.compress((b"\x00"+b"\xff\x00\x00"*16)*16))+chunk(b"IEND",b""))
 s=Session(Path(temp),"test")
 result=feedback_content(json.dumps({"exit_code":0,"stdout":json.dumps({"skills_agent_feedback":1,"attachments":[{"type":"image","path":str(p)}]})}),s)
 assert result[2]["type"]=="image"
 model=OpenAIProvider("deepseek-v4.1-flash","TEST_NOT_REAL",base_url="https://api.deepseek.com",token_counter="deepseek",tokenizer_path=tokenizer,tokenizer_encoding="v41",deepseek_effort="max")
 payload=model._chat_payload("",[{"role":"user","content":result}],[])
 assert payload["messages"][0]["content"][2]["type"]=="image_url"
 counter=model.count_tokens("",[{"role":"user","content":result}],[])
 assert counter>0
records=[json.loads(x) for x in log.read_text().splitlines()]
assert sum(x["event"]=="usage" for x in records)==3, "Duplicate usage frames must not double count"
assert not any(x["event"]=="transport_error" for x in records)
plain=subprocess.run(["/usr/bin/python3","-c","import json,ssl; print(json.dumps({'system_python':'unaffected','native_default_context':ssl._create_default_https_context is ssl.create_default_context}))"],env={**os.environ,"PYTHONPATH":str(assets)},capture_output=True,text=True)
assert plain.returncode==0 and json.loads(plain.stdout)["system_python"]=="unaffected"
assert json.loads(plain.stdout)["native_default_context"] is True
default_context=ssl._create_default_https_context()
assert default_context.verify_mode==ssl.CERT_REQUIRED and default_context.check_hostname is True
assert default_context.cert_store_stats()["x509_ca"]>0
explicit_context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
explicit_context.check_hostname=False
connection=http.client.HTTPSConnection("localhost",context=explicit_context)
assert connection._context is explicit_context and explicit_context.check_hostname is False
(root/"native-result.json").write_text(json.dumps({"model":"deepseek-v4.1-flash","tested_provider_file":sys.modules[OpenAIProvider.__module__].__file__,"system_python_native_context":True,"tls_verify_mode":"CERT_REQUIRED","tls_check_hostname":default_context.check_hostname,"tls_root_ca_count":default_context.cert_store_stats()["x509_ca"],"explicit_context_preserved":True,"network_requests":0,"audit_request_records":sum(x["event"]=="request" for x in records),"audit_usage_records":sum(x["event"]=="usage" for x in records),"wrong_effort_rejected":True,"auxiliary_disabled_thinking_allowed":True,"gateway_max_payload":True,"gateway_local_v41_counter":True,"image_feedback_to_deepseek_payload":True,"image_token_count":counter},indent=2)+"\n")
print("Native image-feedback, v41 image tokenization, metadata audit and effort guard passed offline")
