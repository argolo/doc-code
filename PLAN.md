# Projeto `doc-gub`

## Resumo

Criar uma CLI Python para gerar e aplicar documentação com IA em arquivos Python, JavaScript e TypeScript. Ela seguirá o padrão operacional do Commitar: seleção segura de arquivos, prévia por padrão, diff unificado, confirmação única antes de alterar arquivos, OpenAI/Gemini/Ollama e configuração em `.doc-gub.toml`.

## Funcionamento e interface

- Comando principal: `doc-gub [PATH]`.
  - Sem `PATH`, usa o modo configurado: mudanças Git (prioriza staging; depois arquivos modificados) ou varredura do repositório.
  - Com `PATH`, processa o arquivo ou diretório indicado, como no Commitar.
- Modos: `--output preview` (padrão), `--output apply` e `--dry-run` como alias de prévia; `--yes/-y` elimina a confirmação no modo apply.
- Opções principais:
  - `--coverage missing|minimal|all`: documentar apenas ausentes, ausentes e mínimas, ou todos os símbolos.
  - `--existing-docs preserve|replace`: preservar a descrição manual ao atualizar campos estruturais, ou regenerar por inteiro.
  - `--selection changes|repository`: selecionar mudanças Git ou varrer o repositório; ambos também configuráveis no TOML.
  - `--format google|numpy|sphinx` para Python; JSDoc para JS/TS.
  - Reutilizar `--provider`, `--model`, `--timeout-seconds`, `--max-input-tokens`, `--context-window-tokens` e `--config`.
- Subcomandos: `doc-gub config init` cria `.doc-gub.toml`; `doc-gub config show` exibe a configuração efetiva sem credenciais.

## Implementação

- Estruturar o pacote como o Commitar: CLI Typer, carregamento de configuração, provedores de IA, seleção de escopo, geração de diff, aplicação e erros específicos.
- Usar a mesma precedência: flags → `--config` → variáveis `DOC_GUB_*` → `.doc-gub.toml` → `~/.config/doc-gub/config.toml` → padrões.
- Gerar docstrings para módulos, classes, métodos, funções e funções assíncronas Python; gerar JSDoc para funções, classes, métodos e funções arrow JS/TS.
- Aplicar alterações por edição textual delimitada e diff unificado. Antes de gravar, confirmar que o arquivo não mudou desde a prévia; abortar aquele arquivo se houver divergência.
- Ignorar por padrão arquivos Git-ignored, dependências, ambientes virtuais, builds, arquivos minificados e lockfiles. Permitir padrões adicionais de inclusão/exclusão em configuração.
- Limitar arquivos por requisição, tamanho de conteúdo e tokens, com até três tentativas e fallback entre até três modelos, como no Commitar.
- Exibir por arquivo: símbolos encontrados, símbolos alterados/ignorados, modelo usado, diff e duração. Em apply, gravar somente após a confirmação global.

## Configuração padrão

```toml
[ai]
provider = "ollama"
models = ["qwen2.5-coder:14b", "gemma4:e4b"]
endpoint = "http://localhost:11434/api/generate"
max_input_tokens = 12000
context_window_tokens = 32768
max_output_tokens = 800
temperature = 0.2
timeout_seconds = 60

[documentation]
selection = "changes"
coverage = "missing"
existing_docs = "preserve"
python_format = "google"
javascript_format = "jsdoc"
output = "preview"
confirm = true

[limits]
max_files_per_request = 50
max_file_bytes = 100000
exclude = ["**/node_modules/**", "**/dist/**", "**/build/**", "**/*.min.js", "**/package-lock.json"]
```

## Testes

- Cobrir precedência de configuração, validação das opções e exclusões.
- Cobrir seleção por staging, mudanças não staged, varredura completa e `PATH`.
- Validar geração de cada formato Python e JSDoc, incluindo símbolos aninhados e assíncronos.
- Garantir que prévia não altera arquivos, apply exige confirmação, `--yes` aplica e arquivos alterados após a prévia são rejeitados.
- Simular respostas inválidas, timeout, fallback de modelo e arquivos sem símbolos elegíveis.

## Premissas

- O nome público da CLI e do pacote será `doc-gub`.
- A primeira versão edita apenas Python, JavaScript e TypeScript.
- O modo padrão é `changes`, a cobertura padrão é `missing`, e a atualização de documentação existente preserva a descrição manual.
