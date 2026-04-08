try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    from langchain.text_splitter import RecursiveCharacterTextSplitter

def split_text(documents, chunk_size=500, chunk_overlap=50):
    """将文档分块"""
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len
    )
    
    chunks = []
    for doc in documents:
        splits = text_splitter.split_text(doc['content'])
        for i, split in enumerate(splits):
            chunks.append({
                'content': split,
                'metadata': {
                    **doc['metadata'],
                    'chunk_id': i
                }
            })
    
    return chunks