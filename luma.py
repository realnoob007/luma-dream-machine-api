import os
import json
import logging
import mimetypes
import requests
from urllib.parse import urljoin, urlparse, urlencode, urlunparse
from requests.structures import CaseInsensitiveDict
from sqlalchemy import create_engine, Column, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

from api_types import GenerationItem, Video
from util import update_cookies

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Base = declarative_base()

class Generation(Base):
    __tablename__ = 'generations'
    id = Column(String, primary_key=True)
    prompt = Column(String)
    state = Column(String)
    created_at = Column(DateTime)
    video_url = Column(String)

class ErrCodes:
    NotLogin = 401
    UnknownError = 500

class MyError(Exception):
    def __init__(self, code, message=None):
        self.code = code
        self.message = message

class Sdk:
    headers = {
        'accept': '*/*',
        'accept-language': 'en-US,en;q=0.9',
        'sec-ch-ua': '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'referrer-Policy': 'no-referrer-when-downgrade',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        "origin": "https://lumalabs.ai",
        "referer": "https://lumalabs.ai",
    }

    LOGIN_RE_CAPTCHA_KEY = ''
    SITE_KEY = ''
    API_BASE = os.getenv('LUMA_API_BASE', 'https://internal-api.virginia.labs.lumalabs.ai')

    def __init__(self, cookies=None, username=None, password=None, profile_root=None):
        self.cookies = cookies or []
        self.username = username
        self.password = password
        self.profile_root = profile_root
        if not os.path.exists(profile_root):
            os.makedirs(profile_root, exist_ok=True)
        self.cookies_file = os.path.join(profile_root, 'cookies.json')
        self.after_cookies_updated_callback = self.save_cookies

        if os.path.exists(self.cookies_file):
            with open(self.cookies_file, 'r', encoding='utf8') as f:
                self.cookies = json.load(f)

        self.engine = create_engine('sqlite:///generations.db')
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def save_cookies(self, cookies):
        with open(self.cookies_file, 'w', encoding='utf8') as f:
            json.dump(cookies, f, indent=2)

    def add_access_token(self, access_token):
        cookie = {'name': 'access_token', 'value': access_token, 'domain': 'internal-api.virginia.labs.lumalabs.ai', 'path': '/', 'secure': True, 'httpOnly': True, 'sameSite': 'None'}
        self.cookies.append(cookie)

    def get_generations(self):
        url = f'{self.API_BASE}/api/photon/v1/user/generations/'
        query = {"offset": "0", "limit": "10"}
        u = urlparse(url)
        u = u._replace(query=urlencode(query))
        resp = self.send_get(urlunparse(u))
        items = resp.json()

        gi_list: list[GenerationItem] = []
        for item in items:
            if 'video' in item and item['video'] is not None:
                video = Video(**item['video'])
            else:
                video = None
            gi = GenerationItem(
                id=item['id'],
                prompt=item['prompt'],
                state=item['state'],
                created_at=item['created_at'],
                video=video,
                liked=item.get('liked'),
                estimate_wait_seconds=item.get('estimate_wait_seconds')
            )
            gi_list.append(gi)

        return gi_list

    def prepare_generate(self, prompt, file_path=None, file_end_path=None, aspect_ratio="16:9", expand_prompt=False):
        url = f'{self.API_BASE}/api/photon/v1/generations/'
        payload = {
            "user_prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "expand_prompt": expand_prompt
        }
        if file_path:
            payload['image_url'] = self.upload_image(file_path)
        if file_end_path:
            payload['image_end_url'] = self.upload_image(file_end_path)
        return payload

    def generate(self, prompt, file_path=None, file_end_path=None, aspect_ratio="16:9", expand_prompt=True):
        url = f'{self.API_BASE}/api/photon/v1/generations/'
        payload = self.prepare_generate(prompt, file_path, file_end_path, aspect_ratio, expand_prompt)
        logger.info(f'generate payload={json.dumps(payload, indent=2)}')
        resp = self.send_post_json(url, payload)
        return resp.json()[0]["id"]

    def upload_image(self, file_path):
        signed_upload = self.get_signed_upload(os.path.basename(file_path))
        pre_signed_url = signed_upload['presigned_url']
        public_url = signed_upload['public_url']
        content_type = mimetypes.guess_type(file_path)[0] or 'image/jpeg'
        headers = {'Content-Type': content_type, 'Content-Length': str(os.path.getsize(file_path))}

        with open(file_path, 'rb') as f:
            resp = requests.put(pre_signed_url, data=f, headers=headers)
        if resp.status_code != 200:
            raise Exception(f'Failed to upload image: {resp.status_code}')
        return public_url

    def get_signed_upload(self, filename):
        url = f'{self.API_BASE}/api/photon/v1/generations/file_upload'
        params = {'file_type': 'image', 'filename': filename}
        u = urlparse(url)
        u = u._replace(query=urlencode(params))
        resp = self.send_post(urlunparse(u))
        return resp.json()

    def is_login(self):
        try:
            self.get_generations()
            return True
        except MyError as e:
            if e.code == ErrCodes.NotLogin:
                return False
            raise e

    def send_post_json(self, url, body=None, headers=None):
        headers = headers or {}
        headers['Content-Type'] = 'application/json'
        return self.send_post(url, headers=headers, body=json.dumps(body))

    def send_post(self, url, headers=None, body=None, method='POST'):
        headers = headers or {}
        headers = {**self.headers, **headers} if headers else self.headers
        headers['cookie'] = self.get_cookie_str()
        logger.info(f'sendPost url={url}')
        resp = requests.request(method, url, headers=headers, data=body)
        logger.debug(f'sendPost response({resp.status_code}), url={url}')
        self.check_resp(resp)
        cookies = resp.cookies.get_dict()
        self.update_cookies(cookies)
        return resp

    def send_get(self, url, headers=None):
        headers = headers or {}
        headers = {**self.headers, **headers}
        headers['cookie'] = self.get_cookie_str()
        logger.info(f'sendGet url={url}')
        resp = requests.get(url, headers=headers)
        logger.debug(f'sendGet response({resp.status_code}), url={url}')
        self.check_resp(resp)
        self.update_cookies(resp.cookies)
        return resp

    def update_cookies(self, cookies: dict):
        cookies = [
            {'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path}
            for c in cookies
        ]
        self.cookies = update_cookies(self.cookies, cookies)
        self.after_cookies_updated_callback(self.cookies)

    def get_cookie_str(self):
        return '; '.join([f'{ck["name"]}={ck["value"]}' for ck in self.cookies])

    def get_filename(self, url):
        parsed_url = urlparse(url)
        filename = os.path.basename(parsed_url.path)
        filename = filename.replace(' ', '_')
        return filename

    def check_resp(self, resp):
        if not resp.ok:
            logger.debug(f'headers: {resp.headers}')
            if resp.status_code == 401:
                text = resp.text
                logger.info(f'checkResp status=401, text={text}')
                raise MyError(ErrCodes.NotLogin)
            elif resp.status_code == 429:
                text = resp.text
                logger.info(f'checkResp status=429, text={text}')
                self.remove_access_token()
                raise MyError(ErrCodes.TooManyRequests, 'Too many requests, access token removed')
            else:
                self.throw_resp_error(resp)

    def throw_resp_error(self, resp):
        text = resp.text
        logger.info(f'checkResp status={resp.status_code} text={text[:1024]}')
        raise MyError(ErrCodes.UnknownError, f'HTTP {resp.status_code} {resp.reason}, body={text}')

    def remove_access_token(self):
        self.cookies = [ck for ck in self.cookies if ck['name'] != 'access_token']

    def usage(self):
        url = f'{self.API_BASE}/api/photon/v1/subscription/usage'
        resp = self.send_get(url)
        return resp.json()

    def process_generations(self):
        generations = self.get_generations()
        session = self.Session()
        for gen in generations:
            if gen.video and 'url' in gen.video and gen.video['url']:  # Correctly check if video URL exists
                existing_gen = session.query(Generation).filter_by(id=gen.id).first()
                if not existing_gen:
                    new_gen = Generation(
                        id=gen.id,
                        prompt=gen.prompt,
                        state=gen.state,
                        created_at=datetime.strptime(gen.created_at, '%Y-%m-%dT%H:%M:%S.%fZ'),
                        video_url=gen.video['url']
                    )
                    session.add(new_gen)
        session.commit()
        session.close()
