from fastapi import FastAPI
from pydantic import BaseModel
from agents.dispatcher import dispatch

app = FastAPI()

class UserRequest(BaseModel):
    message: str

@app.post("/chat")
def chat(req: UserRequest):
    return dispatch(req.message)