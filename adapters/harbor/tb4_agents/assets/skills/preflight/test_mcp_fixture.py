from mcp.server.fastmcp import FastMCP
mcp=FastMCP("tb4-preflight",host="127.0.0.1",port=18763)
state={"count":0}
@mcp.tool()
def counter():
 state["count"]+=1
 return state["count"]
if __name__=="__main__":mcp.run(transport="sse")
