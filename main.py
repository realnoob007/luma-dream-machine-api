from typing import Union, Annotated, List

from fastapi import FastAPI, Form, UploadFile, File, Response

import settings
from api_types import GenerationItem, GenerateResponseItem
from luma import Sdk, Generation
import tempfile
import os


app = FastAPI()
sdk = Sdk(username='test', password='test', profile_root='./storage/profile/0')

def load_access_tokens(file_path):
    with open(file_path, 'r') as file:
        return [line.strip() for line in file.readlines()]

access_tokens = load_access_tokens('tokens.txt')
for token in access_tokens:
    sdk.add_access_token(token)

@app.post("/api/v1/generate")
def generate(user_prompt: Annotated[str, Form()], image: Annotated[UploadFile, File()] = None, expand_prompt: Annotated[bool, Form()] = False) -> str:
    image_path = None
    if image:
        suffix = os.path.splitext(image.filename)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            tmp_file.write(image.file.read())
            image_path = tmp_file.name
    return sdk.generate(user_prompt, image_path, expand_prompt)

@app.get('/api/v1/generations')
def get_generations() -> List[GenerationItem]:
    return sdk.get_generations()

@app.get('/api/v1/generations/{id}')
def get_generation_by_id(id: str):
    sdk.process_generations()
    session = sdk.Session()
    generation = session.query(Generation).filter_by(id=id).first()
    session.close()
    if generation:
        return generation
    else:
        return Response(status_code=404, content='任务不存在或正在生成中')
    
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)