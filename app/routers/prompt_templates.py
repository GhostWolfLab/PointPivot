"""Prompt template CRUD API routes"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models import PromptTemplate, get_db
from app.schemas import PromptTemplateCreate, PromptTemplateUpdate, PromptTemplateOut

router = APIRouter(prefix="/api/prompt-templates", tags=["prompt_templates"])


@router.get("", response_model=list[PromptTemplateOut])
def list_templates(db: Session = Depends(get_db)):
    return db.query(PromptTemplate).order_by(PromptTemplate.id.desc()).all()


@router.get("/{tpl_id}", response_model=PromptTemplateOut)
def get_template(tpl_id: int, db: Session = Depends(get_db)):
    tpl = db.query(PromptTemplate).filter(PromptTemplate.id == tpl_id).first()
    if not tpl:
        raise HTTPException(404, "模板不存在")
    return tpl


@router.post("", response_model=PromptTemplateOut)
def create_template(data: PromptTemplateCreate, db: Session = Depends(get_db)):
    tpl = PromptTemplate(
        name=data.name,
        description=data.description,
        content=data.content,
        is_default=data.is_default,
    )
    if data.is_default:
        db.query(PromptTemplate).filter(PromptTemplate.is_default == True).update({"is_default": False})
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


@router.put("/{tpl_id}", response_model=PromptTemplateOut)
def update_template(tpl_id: int, data: PromptTemplateUpdate, db: Session = Depends(get_db)):
    tpl = db.query(PromptTemplate).filter(PromptTemplate.id == tpl_id).first()
    if not tpl:
        raise HTTPException(404, "模板不存在")
    if data.name is not None:
        tpl.name = data.name
    if data.description is not None:
        tpl.description = data.description
    if data.content is not None:
        tpl.content = data.content
    if data.is_default is not None:
        if data.is_default:
            db.query(PromptTemplate).filter(PromptTemplate.is_default == True, PromptTemplate.id != tpl_id).update({"is_default": False})
        tpl.is_default = data.is_default
    db.commit()
    db.refresh(tpl)
    return tpl


@router.delete("/{tpl_id}")
def delete_template(tpl_id: int, db: Session = Depends(get_db)):
    tpl = db.query(PromptTemplate).filter(PromptTemplate.id == tpl_id).first()
    if not tpl:
        raise HTTPException(404, "模板不存在")
    db.delete(tpl)
    db.commit()
    return {"ok": True}
