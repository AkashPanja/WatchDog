"""Person gallery endpoints."""

from __future__ import annotations

import shutil
import uuid

import cv2
import numpy as np
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from ..config import config
from ..db import SessionLocal
from ..models import Person, Photo

router = APIRouter(prefix="/api/persons")

MAX_PHOTOS = 20


class PersonCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    threshold: float = 95.0


class PersonUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    threshold: float | None = None


def _person_or_404(session, person_id: int) -> Person:
    person = session.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="person not found")
    return person


def _check_threshold(value: float) -> float:
    if not 0.0 <= value <= 100.0:
        raise HTTPException(status_code=400, detail="threshold must be between 0 and 100")
    return value


@router.get("")
def list_persons() -> list[dict]:
    with SessionLocal() as session:
        persons = session.query(Person).order_by(Person.name).all()
        return [person.public_dict() for person in persons]


@router.post("")
def create_person(payload: PersonCreate) -> dict:
    name = payload.name.strip()
    _check_threshold(payload.threshold)
    with SessionLocal() as session:
        person = Person(name=name, threshold=payload.threshold)
        session.add(person)
        try:
            session.commit()
        except IntegrityError as err:
            session.rollback()
            raise HTTPException(
                status_code=409, detail="a person with this name already exists"
            ) from err
        session.refresh(person)
        (config.faces_dir / str(person.id)).mkdir(parents=True, exist_ok=True)
        return person.public_dict()


@router.put("/{person_id}")
def update_person(person_id: int, payload: PersonUpdate) -> dict:
    with SessionLocal() as session:
        person = _person_or_404(session, person_id)
        if payload.name is not None:
            person.name = payload.name.strip()
        if payload.threshold is not None:
            person.threshold = _check_threshold(payload.threshold)
        try:
            session.commit()
        except IntegrityError as err:
            session.rollback()
            raise HTTPException(
                status_code=409, detail="a person with this name already exists"
            ) from err
        session.refresh(person)
        return person.public_dict()


@router.delete("/{person_id}")
def delete_person(person_id: int) -> dict:
    with SessionLocal() as session:
        person = _person_or_404(session, person_id)
        session.delete(person)
        session.commit()
    shutil.rmtree(config.faces_dir / str(person_id), ignore_errors=True)
    return {"deleted": person_id}


@router.post("/{person_id}/photos")
def upload_photos(
    person_id: int,
    files: list[UploadFile] = File(...),  # noqa: B008
) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="no files uploaded")
    added: list[int] = []
    with SessionLocal() as session:
        person = _person_or_404(session, person_id)
        if len(person.photos) + len(files) > MAX_PHOTOS:
            raise HTTPException(
                status_code=400,
                detail=f"a person can have at most {MAX_PHOTOS} photos",
            )
        directory = config.faces_dir / str(person.id)
        directory.mkdir(parents=True, exist_ok=True)
        for upload in files:
            data = upload.file.read()
            if not data:
                raise HTTPException(status_code=400, detail=f"{upload.filename}: empty file")
            image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise HTTPException(
                    status_code=400, detail=f"{upload.filename}: not a valid image"
                )
            filename = f"{uuid.uuid4().hex}.jpg"
            path = directory / filename
            if not cv2.imwrite(str(path), image):
                raise HTTPException(status_code=500, detail="failed to store photo")
            photo = Photo(person_id=person.id, filename=filename)
            session.add(photo)
            session.commit()
            session.refresh(photo)
            added.append(photo.id)
        session.refresh(person)
        return {"added": added, "photo_ids": [photo.id for photo in person.photos]}


@router.get("/{person_id}/photos/{photo_id}")
def get_photo(person_id: int, photo_id: int) -> FileResponse:
    with SessionLocal() as session:
        _person_or_404(session, person_id)
        photo = session.get(Photo, photo_id)
        if photo is None or photo.person_id != person_id:
            raise HTTPException(status_code=404, detail="photo not found")
        filename = photo.filename
    path = config.faces_dir / str(person_id) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="photo file missing")
    return FileResponse(path, media_type="image/jpeg")


@router.delete("/{person_id}/photos/{photo_id}")
def delete_photo(person_id: int, photo_id: int) -> dict:
    with SessionLocal() as session:
        _person_or_404(session, person_id)
        photo = session.get(Photo, photo_id)
        if photo is None or photo.person_id != person_id:
            raise HTTPException(status_code=404, detail="photo not found")
        filename = photo.filename
        session.delete(photo)
        session.commit()
    (config.faces_dir / str(person_id) / filename).unlink(missing_ok=True)
    return {"deleted": photo_id}
