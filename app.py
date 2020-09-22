import os
import asyncio
import uuid

from random import randint

from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse #, JSONResponse

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette_wtf import StarletteForm, CSRFProtectMiddleware, csrf_protect

from itsdangerous.url_safe import URLSafeSerializer

from passlib.context import CryptContext

from docker import Client

from database import Database

# DATABASE
DATABASE_URL = 'sqlite:///./app.db' # SQLite cuz I am lazy

# APP
SECRET_KEY = os.environ.get('SECRET_KEY', 'this_should_be_configured')
SERIALIZER = URLSafeSerializer(SECRET_KEY)

db = Database(DATABASE_URL)

app = FastAPI()
app.mount("/dist", StaticFiles(directory="dist"), name="dist")
templates = Jinja2Templates(directory="templates")

app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, session_cookie='hi-mom', same_site='strict')
app.add_middleware(CSRFProtectMiddleware, csrf_secret=SECRET_KEY)

docker = Client()

# This would be a db table but I cannot be fucked
AVALIABLE_CONTAINERS = [
    {
        'image': 'nginx',
        'ports': ['80/tcp']
    },
    {
        'image': 'ssh_demo',
        'ports': ['22/tcp']
    }
]

USED_PORTS = [] # Also should be stored in the DB but fuck you ill populate it on startup

@app.on_event("startup")
async def startup():
    await db.connect()

    async for c in docker.containers.list(all=True): # eat my ass
        for port in c.Ports:
            USED_PORTS.append(port['PublicPort'])

@app.on_event("shutdown")
async def shutdown():
    await db.disconnect()
    await docker.close()

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return templates.TemplateResponse("error.html", {"request": request, "message": f'{exc.status_code}: {exc.detail}'}, status_code=exc.status_code)


async def user(request: Request):
    if 'user' in request.session:
        return await db.get_user(SERIALIZER.loads(request.session['user']))

    return None

async def check_owns_container(user, container_id):
    user_containers = [x.id async for x in db.user_containers(user)]

    if not container_id in user_containers:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    return True

def random_port():
    while True:
        port = randint(49152, 65535)
        if port in USED_PORTS:
            continue

        USED_PORTS.append(port)
        return port


@app.get('/')
async def root(request: Request, user = Depends(user)):
    if not user:
        return templates.TemplateResponse('login.html', {'request': request, 'user': user, 'form': StarletteForm(request)})

    # this whole bit is trash and slow as shit, needs to be re-thought completely
    all_containers = [x async for x in docker.containers.list(all=True)]
    user_containers = [x async for x in db.user_containers(user)]

    containers = {}
    for container in AVALIABLE_CONTAINERS:
        image = container["image"]

        if not (uc := list(filter(lambda x: x.image == image, user_containers))):
            containers[image] = None
            continue

        containers[image] = list(filter(lambda x: x.Id == uc[0].id, all_containers))[0]

    return templates.TemplateResponse('home.html', {'request': request, 'user': user, 'containers': containers, 'form': StarletteForm(request)})

@app.post('/login')
@csrf_protect
async def login(request: Request):
    form = await request.form()
    user = await db.verify_login(form.get('username'), form.get('password'))
    if not user:
        return templates.TemplateResponse('error.html', {'request': request, 'message': 'username or password incorrect'})

    request.session['user'] = SERIALIZER.dumps(user.id)
    # RedirectResponse will try to POST you to the root page
    # https://github.com/tiangolo/fastapi/issues/1498
    return RedirectResponse(url=app.url_path_for('root'), status_code=status.HTTP_303_SEE_OTHER)

@app.get('/logout')
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(app.url_path_for('root'))


# "A" "P" "I"
# Shits a proof of concept I'm not gonna bother with XHR
@app.post('/container')
@csrf_protect
async def user_container_action(request: Request, user = Depends(user)):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    form = await request.form()
    action = form.get('action')
    id = form.get('id')

    if not action in ['start', 'restart', 'stop', 'kill', 'remove']:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if action == 'start' and not id:
        c = list(filter(lambda x: x["image"] == form.get('image'), AVALIABLE_CONTAINERS))[0]
        print({
            'Image': c['image'],
            'HostConfig': {
                'PortBindings': {p: [{'HostPort': str(random_port())}] for p in c['ports']}
            }
        })
        container = await docker.containers.create({
            'Image': c['image'],
            'HostConfig': {
                'PortBindings': {p: [{'HostPort': str(random_port())}] for p in c['ports']}
            }
        }, name = f"{user.username}-{c['image'].replace(':', '')}-{str(uuid.uuid4()).split('-')[0]}")

        await container.start()

        await db.add_user_container(user, id=container.Id, image=c['image'], owner_id=user.id)

        id = container.Id

    if id:
        await check_owns_container(user, id)

        container = await docker.containers.get(id=id)

        if action == 'remove':
            await container.kill()
            await db.remove_user_container(container.Id)

            for port in container.Ports:
                USED_PORTS.remove(port['PublicPort'])

        await getattr(container, action)() # this is cheating

    return RedirectResponse(url=app.url_path_for('root'), status_code=status.HTTP_303_SEE_OTHER)
