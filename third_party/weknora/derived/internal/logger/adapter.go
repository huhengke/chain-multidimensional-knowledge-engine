// Host adapter: send diagnostics to stderr, preserving the JSON-only stdout contract.
package logger
import ("context"; "log")
func Debugf(_ context.Context, f string, args ...any) { log.Printf(f, args...) }
func Infof(_ context.Context, f string, args ...any) { log.Printf(f, args...) }
