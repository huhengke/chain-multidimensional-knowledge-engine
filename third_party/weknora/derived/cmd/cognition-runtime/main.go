// Embedded knowledge runtime. No listener, account database or upstream workbench.
package main
import (
 "bufio"; "bytes"; "encoding/json"; "errors"; "fmt"; "io"; "math"; "net/http"; "os"; "sort"; "strconv"; "strings"; "time"
 "github.com/Tencent/WeKnora/internal/infrastructure/chunker"
 "github.com/Tencent/WeKnora/internal/application/service"
 "github.com/Tencent/WeKnora/internal/application/service/metric"
 "github.com/Tencent/WeKnora/internal/types"
)
const commit="97796c637aa53713edc846bd0a39470d687f0fdf"
type Obj=map[string]any
type Model struct { BaseURL string `json:"base_url"`; Model string `json:"model"`; APIKey string `json:"api_key"` }
type Block struct { Index int `json:"index"`; Text string `json:"text"`; Type string `json:"block_type"`; Heading []string `json:"heading_path"`; Page int `json:"page"`; Anchor string `json:"anchor"` }
type Segment struct { Text string `json:"text"`; UnitType string `json:"unit_type"`; Page int `json:"page"`; Anchor string `json:"anchor"`; Heading []string `json:"heading_path"`; ParentText string `json:"parent_text,omitempty"`; ParentIndex int `json:"parent_index"` }
type Candidate struct { ID string `json:"id"`; Text string `json:"text"`; Lexical string `json:"lexical"`; Semantic string `json:"semantic"` }
type Payload struct {
 Input struct {Content string `json:"content"`; ParseVersion string `json:"parse_version"`} `json:"input"`
 Parsed struct {Blocks []Block `json:"blocks"`} `json:"parsed"`
 Text string `json:"text"`; Query string `json:"query"`; Candidates []Candidate `json:"candidates"`
 Model Model `json:"model"`; Prompt string `json:"prompt"`; Segments []Segment `json:"segments"`
 Retrieved []int `json:"retrieved"`; Relevant []int `json:"relevant"`; K int `json:"k"`
}
func call(m Model, endpoint string, body Obj) (Obj,error) {
 if m.BaseURL=="" || m.Model=="" { return nil,errors.New("MODEL_NOT_CONFIGURED") }
 raw,_:=json.Marshal(body); req,err:=http.NewRequest("POST",strings.TrimRight(m.BaseURL,"/")+endpoint,bytes.NewReader(raw)); if err!=nil{return nil,err}
 req.Header.Set("Content-Type","application/json"); if m.APIKey!="" {req.Header.Set("Authorization","Bearer "+m.APIKey)}
 c:=http.Client{Timeout:90*time.Second,CheckRedirect:func(_ *http.Request,_ []*http.Request)error{return errors.New("redirect refused")}}
 r,err:=c.Do(req);if err!=nil{return nil,errors.New("MODEL_CONNECTION_FAILED")};defer r.Body.Close()
 if r.StatusCode!=200 {return nil,fmt.Errorf("MODEL_HTTP_%d",r.StatusCode)}
 var out Obj; err=json.NewDecoder(io.LimitReader(r.Body,16<<20)).Decode(&out);return out,err
}
func run(op string,p Payload)(Obj,error) {
 switch op {
 case "health":return Obj{"ready":true,"upstream_commit":commit,"runtime_version":"weknora-embedded-v1","capabilities":[]string{"parse","segment","index","retrieve","neighbors","rerank","graph","wiki","evaluate"}},nil
 case "parse":
  blocks:=[]Block{};for page,text:=range strings.Split(strings.ReplaceAll(p.Input.Content,"\r\n","\n"),"\f") {if strings.TrimSpace(text)!=""{blocks=append(blocks,Block{Index:len(blocks),Text:text,Type:"paragraph",Page:page+1,Heading:[]string{},Anchor:fmt.Sprintf("page-%d",page+1)})}}
  return Obj{"parsed":Obj{"blocks":blocks,"parse_version":"weknora-host-text-v1","unavailable":[]any{}}},nil
 case "segment":
  out:=[]Segment{};parentOffset:=0
  for _,b:=range p.Parsed.Blocks {
   split:=chunker.SplitParentChild(b.Text,chunker.SplitterConfig{ChunkSize:1600,ChunkOverlap:160,Strategy:"auto"},chunker.SplitterConfig{ChunkSize:512,ChunkOverlap:80,Strategy:"auto"})
   for _,c:=range split.Children {parent:="";idx:=-1;if c.ParentIndex>=0 {parent=split.Parents[c.ParentIndex].Content;idx=parentOffset+c.ParentIndex}; heading:=[]string{};if c.ContextHeader!=""{heading=append(heading,c.ContextHeader)}
    if strings.TrimSpace(c.Content)!="" {out=append(out,Segment{Text:c.Content,UnitType:"section",Page:b.Page,Anchor:fmt.Sprintf("%s-runes-%d-%d",b.Anchor,c.Start,c.End),Heading:heading,ParentText:parent,ParentIndex:idx})}}
   parentOffset+=len(split.Parents)
  };return Obj{"segments":out},nil
 case "index":
  response,err:=call(p.Model,"/embeddings",Obj{"model":p.Model.Model,"input":[]string{p.Text}});if err!=nil{return nil,err}
  rows,ok:=response["data"].([]any);if !ok||len(rows)!=1{return nil,errors.New("INVALID_EMBEDDING")};row,ok:=rows[0].(map[string]any);if !ok{return nil,errors.New("INVALID_EMBEDDING")};v,ok:=row["embedding"].([]any);if !ok||len(v)<2{return nil,errors.New("INVALID_EMBEDDING")}
  vec:=[]string{};nonzero:=false;for _,x:=range v {n,ok:=x.(float64);if !ok||math.IsInf(n,0)||math.IsNaN(n){return nil,errors.New("INVALID_EMBEDDING")};nonzero=nonzero||n!=0;vec=append(vec,strconv.FormatFloat(n,'f',9,64))};if !nonzero{return nil,errors.New("ZERO_EMBEDDING")};return Obj{"vector":vec},nil
 case "retrieve","neighbors":
  v,k:=[]*types.IndexWithScore{},[]*types.IndexWithScore{}
  for _,c:=range p.Candidates {a,_:=strconv.ParseFloat(c.Semantic,64);b,_:=strconv.ParseFloat(c.Lexical,64);if a>0 {v=append(v,&types.IndexWithScore{ChunkID:c.ID,Score:a})};if b>0{k=append(k,&types.IndexWithScore{ChunkID:c.ID,Score:b})}}
  fused:=service.Fuse(v,k);sort.SliceStable(fused,func(i,j int)bool{if fused[i].Score==fused[j].Score{return fused[i].ChunkID<fused[j].ChunkID};return fused[i].Score>fused[j].Score})
  out:=[]Obj{};for _,r:=range fused{out=append(out,Obj{"id":r.ChunkID,"score":strconv.FormatFloat(r.Score,'f',9,64)})};return Obj{"candidates":out},nil
 case "rerank":
  if len(p.Candidates)==0{return Obj{"candidates":[]any{}},nil};documents:=[]string{};for _,c:=range p.Candidates{documents=append(documents,c.Text)}
  response,err:=call(p.Model,"/rerank",Obj{"model":p.Model.Model,"query":p.Query,"documents":documents,"top_n":len(documents)});if err!=nil{return nil,err}
  results,ok:=response["results"].([]any);if !ok||len(results)!=len(documents){return nil,errors.New("INVALID_RERANK")};out:=[]Obj{};seen:=map[int]bool{}
  for _,raw:=range results{r,ok:=raw.(map[string]any);if !ok{return nil,errors.New("INVALID_RERANK")};n,ok:=r["index"].(float64);i:=int(n);if !ok||float64(i)!=n||i<0||i>=len(documents)||seen[i]{return nil,errors.New("INVALID_RERANK")};seen[i]=true;out=append(out,Obj{"id":p.Candidates[i].ID,"score":r["relevance_score"]})};return Obj{"candidates":out},nil
 case "graph","wiki":
  text:=strings.Builder{};for _,s:=range p.Segments {text.WriteString(s.Text+"\n")}
  response,err:=call(p.Model,"/chat/completions",Obj{"model":p.Model.Model,"messages":[]Obj{{"role":"system","content":p.Prompt},{"role":"user","content":text.String()}},"temperature":0});if err!=nil{return nil,err}
  choices,ok:=response["choices"].([]any);if !ok||len(choices)==0{return nil,errors.New("INVALID_GRAPH")};choice,ok:=choices[0].(map[string]any);if !ok{return nil,errors.New("INVALID_GRAPH")};message,ok:=choice["message"].(map[string]any);if !ok{return nil,errors.New("INVALID_GRAPH")};content,_:=message["content"].(string)
  content=strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(strings.TrimPrefix(content,"```json"),"```"),"```"))
  if op=="wiki" {var profile Obj;if json.Unmarshal([]byte(content),&profile)!=nil{return nil,errors.New("INVALID_WIKI_JSON")};summary,ok:=profile["summary"].(string);if !ok||strings.TrimSpace(summary)==""{return nil,errors.New("EMPTY_WIKI")};return Obj{"wiki":profile,"status":"inferred"},nil}
  var entities []struct{Title string `json:"title"`;Type string `json:"type"`};if json.Unmarshal([]byte(content),&entities)!=nil{return nil,errors.New("INVALID_GRAPH_JSON")}
  graph:=map[string][]string{};for _,e:=range entities{if e.Title!=""&&strings.Contains(text.String(),e.Title){for _,s:=range p.Segments{if strings.Contains(s.Text,e.Title){graph[e.Title]=append(graph[e.Title],s.Anchor)}}}};return Obj{"graph":graph},nil
 case "evaluate":
  if p.K<=0{p.K=10};if len(p.Retrieved)>p.K{p.Retrieved=p.Retrieved[:p.K]};input:=&types.MetricInput{RetrievalGT:[][]int{p.Relevant},RetrievalIDs:p.Retrieved}
  return Obj{"recall":metric.NewRecallMetric().Compute(input),"mrr":metric.NewMRRMetric().Compute(input),"ndcg":metric.NewNDCGMetric(p.K).Compute(input)},nil
 };return nil,errors.New("OPERATION_UNSUPPORTED")
}
func main(){
 scanner:=bufio.NewScanner(os.Stdin);scanner.Buffer(make([]byte,65536),32<<20)
 for scanner.Scan(){func(){
  var req struct{Protocol string `json:"protocol_version"`;RequestID string `json:"request_id"`;TraceID string `json:"trace_id"`;Runtime string `json:"runtime_version"`;Operation string `json:"operation"`;Payload Payload `json:"payload"`}
  err:=json.Unmarshal(scanner.Bytes(),&req);response:=Obj{"protocol_version":"weknora-bridge/v1","request_id":req.RequestID,"trace_id":req.TraceID,"operation":req.Operation,"runtime_version":"weknora-embedded-v1"};start:=time.Now()
  defer func(){if recover()!=nil{response["error"]=Obj{"code":"RUNTIME_FAILED","message":"运行时返回结构异常"}};response["duration_ms"]=time.Since(start).Milliseconds();json.NewEncoder(os.Stdout).Encode(response)}()
  if err!=nil||req.Protocol!="weknora-bridge/v1"||req.Runtime!="weknora-embedded-v1"{response["error"]=Obj{"code":"PROTOCOL_INVALID","message":"无效的协议请求"};return}
  result,err:=run(req.Operation,req.Payload);if err!=nil{response["error"]=Obj{"code":"RUNTIME_OPERATION_FAILED","message":err.Error()}}else{response["result"]=result}
 }()};if scanner.Err()!=nil{os.Exit(2)}
}
