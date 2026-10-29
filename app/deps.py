from arq import ArqRedis
from fastapi import Request

from app.storage import Storage


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_queue(request: Request) -> ArqRedis:
    return request.app.state.arq
