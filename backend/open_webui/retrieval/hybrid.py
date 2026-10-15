"""Fixed rank fusion, not a mixture of incomparable raw scores."""


def reciprocal_rank_fusion(*rankings, count=5):
    scores = {}
    order = {}
    for ranking in rankings:
        for rank, ref in enumerate(dict.fromkeys(ranking), 1):
            order.setdefault(ref, len(order))
            scores[ref] = scores.get(ref, 0.0) + 1.0 / (60 + rank)
    return [(ref, scores[ref]) for ref in sorted(scores, key=lambda ref: (-scores[ref], order[ref]))[:count]]
