// Set helpers extracted from upstream metric/common.go; text generation metrics not included.
package metric
func ToSet[T comparable](li []T) map[T]struct{} {
    res := make(map[T]struct{}, len(li))
    for _, v := range li { res[v] = struct{}{} }; return res
}
func SliceMap[T any, Y any](li []T, fn func(T) Y) []Y {
    res := make([]Y, len(li)); for i, v := range li { res[i] = fn(v) }; return res
}
func Hit[T comparable](li []T, set map[T]struct{}) int {
    count := 0; for _, v := range li { if _, exist := set[v]; exist { count++ } }; return count
}
