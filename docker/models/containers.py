import json
from copy import copy

from .abc import Collection
from ..constants import API_PATH
from . import Container

class Containers(Collection):
    async def list(self, **kwargs):
        async with self._client.session.get(API_PATH["containers"], params=kwargs) as r:
            for x in await r.json():
                yield Container(self._client, x)

    async def get(self, **kwargs): # Should this use the docker container info endpoint instead?
        for k, v in copy(kwargs).items():
            kwargs[k] = [v]

        async for c in self.list(filters=json.dumps(kwargs), limit=1, all=True):
            return c

    async def create(self, data: dict, name = None):
        params = None
        if name:
            params = { 'name': name }

        async with self._client.session.post(API_PATH["container_create"], json=data, params=params) as r:
            return Container(self._client, await r.json())
