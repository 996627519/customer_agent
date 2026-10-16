"""MySQL full-text/keyword policy retriever with source metadata."""


class MySqlPolicyRetriever:
    def __init__(self, database, top_k: int = 5):
        self.database, self.top_k = database, top_k

    def search(self, query: str, *, region: str | None = None, category: str | None = None):
        return self.database.search_policies(query, region=region, category=category, limit=self.top_k)
