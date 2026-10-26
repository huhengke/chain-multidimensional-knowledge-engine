// Minimal host DTOs for extracted algorithms; upstream tenancy and product models are excluded.
package types
type IndexWithScore struct { ChunkID string; Score float64 }
type RetrieveResult struct { RetrieverEngineType string; RetrieverType string; Results []*IndexWithScore }
const VectorRetrieverType = "vector"
type MetricInput struct { RetrievalGT [][]int; RetrievalIDs []int; GeneratedTexts string; GeneratedGT string }
