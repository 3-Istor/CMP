export type TokenKind = "keyword" | "string" | "comment" | "number" | "builtin" | "call" | "plain";

export interface Token {
  kind: TokenKind;
  text: string;
}

const KEYWORDS = new Set(
  "and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield match case".split(
    " ",
  ),
);
const BUILTINS = new Set(
  "print len range dict list set tuple str int float sum min max sorted enumerate zip open round abs map filter any all isinstance".split(
    " ",
  ),
);

// Order matters: comments and strings first, so a "#" inside a string stays a string.
const PATTERN =
  /(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|[rbfu]{0,2}"(?:\\.|[^"\\\n])*"|[rbfu]{0,2}'(?:\\.|[^'\\\n])*')|(\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)(?=\s*\()|([A-Za-z_][A-Za-z0-9_]*)/g;

/** A small Python tokenizer for read-only code views (sandbox runs). */
export function tokenizePython(code: string): Token[] {
  const tokens: Token[] = [];
  let last = 0;
  for (const match of code.matchAll(PATTERN)) {
    const index = match.index ?? 0;
    if (index > last) tokens.push({ kind: "plain", text: code.slice(last, index) });
    const [text, comment, string, number, call, word] = match;
    let kind: TokenKind = "plain";
    if (comment) kind = "comment";
    else if (string) kind = "string";
    else if (number) kind = "number";
    else if (call) kind = KEYWORDS.has(call) ? "keyword" : BUILTINS.has(call) ? "builtin" : "call";
    else if (word) kind = KEYWORDS.has(word) ? "keyword" : BUILTINS.has(word) ? "builtin" : "plain";
    tokens.push({ kind, text });
    last = index + text.length;
  }
  if (last < code.length) tokens.push({ kind: "plain", text: code.slice(last) });
  return tokens;
}
