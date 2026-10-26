// Extracted from upstream image_resolver.go at locked commit; no remote image fetching.
package docparser
import "regexp"
var reLinkedImage = regexp.MustCompile(
    `\[!\[([^\]]*)\]\(([^()\s]*(?:\([^)]*\)[^()\s]*)*)\)\]` +
    `\([^()\s]*(?:\([^)]*\)[^()\s]*)*\)`,
)
func UnwrapLinkedImages(markdown string) string {
    return reLinkedImage.ReplaceAllString(markdown, "![$1]($2)")
}
