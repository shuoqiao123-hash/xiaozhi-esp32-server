import pdfplumber
import os

def extract_text_from_pdf(pdf_path):
    """从 PDF 中提取文本"""
    text_pages = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text()
            if text:
                text_pages.append({
                    'page': page_num + 1,
                    'text': text
                })
    
    return text_pages

def extract_text_from_directory(pdf_dir):
    """从目录中提取所有 PDF 文本"""
    all_documents = []
    
    for filename in os.listdir(pdf_dir):
        if filename.endswith('.pdf'):
            pdf_path = os.path.join(pdf_dir, filename)
            text_pages = extract_text_from_pdf(pdf_path)
            
            for page in text_pages:
                all_documents.append({
                    'content': page['text'],
                    'metadata': {
                        'source': filename,
                        'page': page['page'],
                        'category': filename.replace('.pdf', '')
                    }
                })
    
    return all_documents