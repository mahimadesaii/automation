import re

def get_ngrams(text: str, n: int = 3) -> set:
    """
    Extracts set of word n-grams from text.
    """
    if not text:
        return set()
    # Normalize text to lowercase words
    words = re.findall(r'\b\w+\b', text.lower())
    if len(words) < n:
        return set(words)
    return set(zip(*[words[i:] for i in range(n)]))

def calculate_ngram_similarity(text1: str, text2: str, n: int = 3) -> float:
    """
    Computes Jaccard similarity index between n-gram sets of two texts.
    Returns value between 0.0 (completely distinct) and 1.0 (identical).
    """
    ngrams1 = get_ngrams(text1, n=n)
    ngrams2 = get_ngrams(text2, n=n)
    
    if not ngrams1 or not ngrams2:
        return 0.0
    
    intersection = len(ngrams1.intersection(ngrams2))
    union = len(ngrams1.union(ngrams2))
    
    if union == 0:
        return 0.0
    return intersection / union

def check_section_duplication(current_content: str, previous_contents: list, threshold: float = 0.7, n: int = 3) -> tuple:
    """
    Checks if current_content shares > threshold similarity with any previous section.
    Returns: (is_duplicate: bool, max_similarity: float, matching_index: int)
    """
    if not previous_contents or not current_content:
        return False, 0.0, -1
    
    max_sim = 0.0
    match_idx = -1
    
    for idx, prev_text in enumerate(previous_contents):
        sim = calculate_ngram_similarity(current_content, prev_text, n=n)
        if sim > max_sim:
            max_sim = sim
            match_idx = idx
            
    is_dup = max_sim >= threshold
    return is_dup, max_sim, match_idx
