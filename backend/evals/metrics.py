def recall_at_k(retrieved: list[str], relevant: list[str], k: int = 5) -> float:
    expected = set(relevant)
    if not expected:
        return 1.0
    return len(set(retrieved[:k]) & expected) / len(expected)


def reciprocal_rank(retrieved: list[str], relevant: list[str]) -> float:
    expected = set(relevant)
    for rank, item in enumerate(retrieved, 1):
        if item in expected:
            return 1.0 / rank
    return 0.0
