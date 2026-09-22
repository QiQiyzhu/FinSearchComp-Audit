import uvicorn

if __name__ == "__main__":
    uvicorn.run("research_workbench.api:create_app", factory=True, host="127.0.0.1", port=8090, workers=1)
