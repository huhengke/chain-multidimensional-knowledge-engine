package service

import (
	"context"
	"errors"
	"sort"
	"sync"
	"testing"

	"github.com/Tencent/WeKnora/internal/models/chat"
	"github.com/Tencent/WeKnora/internal/types"
)

// TestMergeCitationsIntoItems_PopulatesSourceChunksOnCandidates verifies that
// citations returned by the chunk-classification pass are attached back onto
// the matching candidate items while non-cited candidates are left untouched.
func TestMergeCitationsIntoItems_PopulatesSourceChunksOnCandidates(t *testing.T) {
	entities := []extractedItem{
		{Name: "Acme", Slug: "entity/acme"},
		{Name: "Beta", Slug: "entity/beta"},
	}
	concepts := []extractedItem{
		{Name: "RAG", Slug: "concept/rag"},
	}
	citations := map[string][]string{
		"entity/acme": {"chunk-1", "chunk-3"},
		"concept/rag": {"chunk-2"},
	}

	gotE, gotC, uncited := mergeCitationsIntoItems(entities, concepts, citations, nil)

	if len(gotE) != 2 || len(gotC) != 1 {
		t.Fatalf("expected 2 entities + 1 concept, got %d + %d", len(gotE), len(gotC))
	}
	acme := findBySlug(gotE, "entity/acme")
	if acme == nil {
		t.Fatalf("entity/acme missing from result")
	}
	if !equalStrings(acme.SourceChunks, []string{"chunk-1", "chunk-3"}) {
		t.Errorf("entity/acme source_chunks = %v, want [chunk-1 chunk-3]", acme.SourceChunks)
	}
	beta := findBySlug(gotE, "entity/beta")
	if beta == nil {
		t.Fatalf("entity/beta missing from result")
	}
	if len(beta.SourceChunks) != 0 {
		t.Errorf("entity/beta should have no citations, got %v", beta.SourceChunks)
	}
	rag := findBySlug(gotC, "concept/rag")
	if rag == nil {
		t.Fatalf("concept/rag missing")
	}
	if !equalStrings(rag.SourceChunks, []string{"chunk-2"}) {
		t.Errorf("concept/rag source_chunks = %v, want [chunk-2]", rag.SourceChunks)
	}
	if uncited != 1 {
		t.Errorf("uncited = %d, want 1", uncited)
	}
}

// TestMergeCitationsIntoItems_AddsNewSlugsAndUnionsChunksAcrossBatches checks
// that genuinely new slugs (ones Pass 0 missed) are appended to the right
// type slice, and that a slug surfacing in two batches ends up with the union
// of its source chunks.
func TestMergeCitationsIntoItems_AddsNewSlugsAndUnionsChunksAcrossBatches(t *testing.T) {
	entities := []extractedItem{
		{Name: "Known", Slug: "entity/known"},
	}
	concepts := []extractedItem{}

	newSlugs := []newSlugFromCitation{
		{
			Type:         "entity",
			Name:         "Fresh Entity",
			Slug:         "entity/fresh",
			Description:  "desc",
			Details:      "details",
			SourceChunks: []string{"c001", "c002"},
		},
		{
			// Same slug as above, appears in another batch — must union.
			Type:         "entity",
			Name:         "Fresh Entity",
			Slug:         "entity/fresh",
			SourceChunks: []string{"c002", "c003"},
		},
		{
			Type:         "concept",
			Name:         "New Concept",
			Slug:         "concept/new-concept",
			SourceChunks: []string{"c010"},
		},
		{
			// Duplicate of an existing candidate — should NOT produce a
			// duplicate entry (Known already exists in `entities`).
			Type:         "entity",
			Name:         "Known",
			Slug:         "entity/known",
			SourceChunks: []string{"c020"},
		},
	}

	gotE, gotC, _ := mergeCitationsIntoItems(entities, concepts, nil, newSlugs)

	if len(gotE) != 2 {
		t.Fatalf("expected 2 entities, got %d (%+v)", len(gotE), gotE)
	}
	if len(gotC) != 1 {
		t.Fatalf("expected 1 concept, got %d (%+v)", len(gotC), gotC)
	}
	fresh := findBySlug(gotE, "entity/fresh")
	if fresh == nil {
		t.Fatalf("entity/fresh missing")
	}
	sort.Strings(fresh.SourceChunks)
	if !equalStrings(fresh.SourceChunks, []string{"c001", "c002", "c003"}) {
		t.Errorf("entity/fresh source_chunks = %v, want union [c001 c002 c003]", fresh.SourceChunks)
	}
	newC := findBySlug(gotC, "concept/new-concept")
	if newC == nil || !equalStrings(newC.SourceChunks, []string{"c010"}) {
		t.Errorf("concept/new-concept missing or wrong chunks: %+v", newC)
	}
}

// TestSplitChunksIntoCitationBatches_RespectsBudgetAndOrder verifies that the
// batcher never puts too many runes in one batch, preserves document order,
// and that an oversized chunk gets its own batch. Sizes are derived from
// maxRunesPerCitationBatch so the fixture stays valid when the budget is tuned.
func TestSplitChunksIntoCitationBatches_RespectsBudgetAndOrder(t *testing.T) {
	mk := func(idx int, runes int, id string) *types.Chunk {
		return &types.Chunk{
			ID:         id,
			ChunkIndex: idx,
			Content:    repeatRune('a', runes),
			ChunkType:  types.ChunkTypeText,
		}
	}
	// Just over half the budget each: any two of them together exceed the
	// limit, so they must land in separate batches.
	half := maxRunesPerCitationBatch/2 + 1
	oversized := maxRunesPerCitationBatch * 2
	chunks := []*types.Chunk{
		mk(0, half, "id-0"),
		mk(1, half, "id-1"),
		mk(2, half, "id-2"),
		mk(3, oversized, "id-big"),
		mk(4, 1000, "id-small"),
	}
	batches := splitChunksIntoCitationBatches(chunks)
	if len(batches) < 3 {
		t.Fatalf("expected at least 3 batches, got %d", len(batches))
	}
	// All input IDs should show up in some batch, exactly once, in order.
	seen := []string{}
	for bi, b := range batches {
		for _, c := range b.chunks {
			seen = append(seen, c.ID)
		}
		total := 0
		for _, c := range b.chunks {
			total += len([]rune(c.Content))
		}
		// Only a single oversized chunk may break the budget, and it must
		// be alone so the rest of the document isn't dragged past the
		// model's context window with it.
		if total > maxRunesPerCitationBatch && len(b.chunks) > 1 {
			t.Errorf("batch %d holds %d runes over budget %d with %d chunks",
				bi, total, maxRunesPerCitationBatch, len(b.chunks))
		}
	}
	wantOrder := []string{"id-0", "id-1", "id-2", "id-big", "id-small"}
	if !equalStrings(seen, wantOrder) {
		t.Errorf("batch order = %v, want %v", seen, wantOrder)
	}

	// Verify the typed handle table is populated per batch.
	for bi, b := range batches {
		if b.handles.Len() != len(b.chunks) {
			t.Errorf("batch %d handle count %d != chunk count %d", bi, b.handles.Len(), len(b.chunks))
		}
	}

	for _, b := range batches {
		if len(b.chunks) == 1 && b.chunks[0].ID == "id-big" {
			return
		}
	}
	t.Errorf("oversized chunk %q shared a batch instead of taking its own", "id-big")
}

// flakyCiteChatModel answers the first len(answers) calls from its queue and
// fails every later call. The error is deliberately non-transient so
// generateWithTemplateResult short-circuits its retry budget instead of
// sleeping through 2s+4s backoff inside a unit test — batch accounting, not
// transport behaviour, is what these cases cover.
type flakyCiteChatModel struct {
	mu      sync.Mutex
	answers []string
	next    int
}

func (m *flakyCiteChatModel) Chat(
	context.Context,
	[]chat.Message,
	*chat.ChatOptions,
) (*types.ChatResponse, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	answer := ""
	if m.next < len(m.answers) {
		answer = m.answers[m.next]
	}
	m.next++
	if answer == "" {
		return nil, errors.New("chat completion failed: deterministic test failure")
	}
	return &types.ChatResponse{Content: answer}, nil
}

func (m *flakyCiteChatModel) ChatStream(
	context.Context,
	[]chat.Message,
	*chat.ChatOptions,
) (<-chan types.StreamResponse, error) {
	return nil, nil
}

func (m *flakyCiteChatModel) GetModelName() string { return "flaky" }
func (m *flakyCiteChatModel) GetModelID() string   { return "flaky" }

// TestClassifyChunkCitations_CountsFailedBatches is the regression guard for
// the silent-success defect: with the gateway down every batch fails and the
// merged result is empty, which is indistinguishable from a document that
// genuinely cites nothing unless failedBatches/batchCount are reported.
func TestClassifyChunkCitations_CountsFailedBatches(t *testing.T) {
	// Two just-over-half-budget chunks → exactly two batches.
	half := maxRunesPerCitationBatch/2 + 1
	chunks := []*types.Chunk{
		{ID: "chunk-a", ChunkIndex: 0, Content: repeatRune('a', half), ChunkType: types.ChunkTypeText},
		{ID: "chunk-b", ChunkIndex: 1, Content: repeatRune('b', half), ChunkType: types.ChunkTypeText},
	}
	candidates := `- slug: entity/acme, type: entity, name: "Acme", description: d`
	service := &wikiIngestService{}

	t.Run("every batch fails", func(t *testing.T) {
		citations, newSlugs, batchCount, failed := service.classifyChunkCitations(
			context.Background(), &flakyCiteChatModel{}, candidates, chunks, "zh", nil)
		if batchCount != 2 {
			t.Fatalf("batch count = %d, want 2", batchCount)
		}
		if failed != batchCount {
			t.Errorf("failed batches = %d, want %d", failed, batchCount)
		}
		if len(citations) != 0 || len(newSlugs) != 0 {
			t.Errorf("expected empty results, got %v / %v", citations, newSlugs)
		}
	})

	t.Run("one batch survives", func(t *testing.T) {
		// Handles are batch-local, so either batch can be the one that
		// answers; the counts are what's deterministic here.
		model := &flakyCiteChatModel{answers: []string{`{"citations":{"entity/acme":["c000"]}}`}}
		citations, _, batchCount, failed := service.classifyChunkCitations(
			context.Background(), model, candidates, chunks, "zh", nil)
		if batchCount != 2 {
			t.Fatalf("batch count = %d, want 2", batchCount)
		}
		if failed != 1 {
			t.Errorf("failed batches = %d, want 1", failed)
		}
		if len(citations["entity/acme"]) != 1 {
			t.Errorf("the surviving batch's citation was dropped: %v", citations)
		}
	})
}

// --- helpers ---

func findBySlug(items []extractedItem, slug string) *extractedItem {
	for i := range items {
		if items[i].Slug == slug {
			return &items[i]
		}
	}
	return nil
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func repeatRune(r rune, n int) string {
	out := make([]rune, n)
	for i := range out {
		out[i] = r
	}
	return string(out)
}
