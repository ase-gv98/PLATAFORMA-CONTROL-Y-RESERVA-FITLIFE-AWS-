from django.db import IntegrityError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


def fitlife_exception_handler(exc, context):
    if isinstance(exc, IntegrityError):
        return Response(
            {'detail': 'No se pudo guardar el registro porque ya existe un dato duplicado.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return exception_handler(exc, context)
