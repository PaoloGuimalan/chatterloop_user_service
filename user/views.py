from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated


class UserAuthentication(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {"status": True, "message": "Delete type is not allowed"},
            status=status.HTTP_200_OK,
        )
