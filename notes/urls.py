from django.urls import path
from .views import NoteListView, NoteDetailView, NoteReorderView

app_name = "notes"
urlpatterns = [
    path("notes/", NoteListView.as_view(), name="list"),
    path("notes/reorder/", NoteReorderView.as_view(), name="reorder"),
    path("notes/<uuid:note_id>/", NoteDetailView.as_view(), name="detail"),
]
