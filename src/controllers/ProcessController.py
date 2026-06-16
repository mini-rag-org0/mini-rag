from.BaseController import BaseController
from .ProjectController import ProjectController
import os
import asyncio
from langchain_community.document_loaders import TextLoader
from langchain_community.document_loaders import PyMuPDFLoader
from models import ProcessingEnum
from typing import List
from dataclasses import dataclass


@dataclass
class Document:
    page_content :str
    metadata : dict

class ProcessController(BaseController):

    def __init__(self, project_id: str):
        super().__init__()

        self.project_id = project_id
        self.project_path = ProjectController().get_project_path(project_id=project_id)


    def get_file_extenion(self, file_id: str):
        return os.path.splitext(file_id)[-1]
    
    def get_file_loader(self, file_id:str):

        file_ext = self.get_file_extenion(file_id=file_id)
        file_path = os.path.join(
            self.project_path,
            file_id

        )

        if not os.path.exists(file_path):
            return None

        if file_ext == ProcessingEnum.TXT.value:
            return TextLoader(file_path, encoding="utf-8")
        
        if file_ext == ProcessingEnum.PDF.value:
            return PyMuPDFLoader(file_path)
        
        return None
    

    async def get_file_content(self, file_id: str):

        loader = self.get_file_loader(file_id=file_id)
        if loader:

            return await asyncio.to_thread(loader.load)
        
        return None
    
    def process_file_content(self, file_content: list, file_id: str, 
                             chunk_size: int=100, overlap_size: int=20):
        

        file_content_texts = [
            rec.page_content
            for rec in file_content
        ]
        

        file_content_metadata= [
            rec.metadata
            for rec in file_content
        ]

        chunks = self.process_simpler_splitter(
            texts=file_content_texts,
            metadatas=file_content_metadata,
            chunk_size=chunk_size,
            overlap_size=overlap_size,
        )
        return chunks
    
    def process_simpler_splitter(self, texts: List[str], metadatas: List[dict],
                                 chunk_size: int, overlap_size: int = 0,
                                 splitter_tag: str = "\n"):

        full_text = " ".join(texts)
        combined_metadata = metadatas[0] if metadatas else {}

        # split by splitter_tag
        lines = [ doc.strip() for doc in full_text.split(splitter_tag) if len(doc.strip()) > 1 ]

        chunks = []
        current_chunk_lines = []
        current_len = 0

        for line in lines:
            current_chunk_lines.append(line)
            current_len += len(line) + len(splitter_tag)
            if current_len >= chunk_size:
                chunks.append(Document(
                    page_content=splitter_tag.join(current_chunk_lines).strip(),
                    metadata=combined_metadata.copy()
                ))

                # Implement overlap: keep trailing lines up to overlap_size characters
                overlap_lines = []
                overlap_len = 0
                for prev_line in reversed(current_chunk_lines):
                    if overlap_len + len(prev_line) > overlap_size:
                        break
                    overlap_lines.insert(0, prev_line)
                    overlap_len += len(prev_line)
                current_chunk_lines = overlap_lines
                current_len = overlap_len

        if len(current_chunk_lines) > 0:
            chunks.append(Document(
                page_content=splitter_tag.join(current_chunk_lines).strip(),
                metadata=combined_metadata.copy()
            ))

        return chunks
