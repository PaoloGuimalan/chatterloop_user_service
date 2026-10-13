from django.urls import path
from .views import NoteListView, NoteDetailView

app_name = "notes"
urlpatterns = [
    path("notes/", NoteListView.as_view(), name="list"),
    path("notes/<uuid:note_id>/", NoteDetailView.as_view(), name="detail"),
]
