package service
import ("context"; "github.com/Tencent/WeKnora/internal/types")
func Fuse(vector, keyword []*types.IndexWithScore) []*types.IndexWithScore {
    var v, k [][]*types.IndexWithScore
    if len(vector)>0 { v=[][]*types.IndexWithScore{vector} }
    if len(keyword)>0 { k=[][]*types.IndexWithScore{keyword} }
    return fuseOrDeduplicate(context.Background(), v, k, nil)
}
