from sqlalchemy import create_engine, Column, String, Integer, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "sqlite:///./test.db"  # You can change this to your preferred database URL

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Generation(Base):
    __tablename__ = "generations"

    id = Column(String, primary_key=True, index=True)
    prompt = Column(String, index=True)
    state = Column(String)
    created_at = Column(DateTime)
    video_url = Column(String)
    video_width = Column(Integer)
    video_height = Column(Integer)
    video_thumbnail = Column(String)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
