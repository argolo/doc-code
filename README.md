# doc-gub

`doc-gub` gera documentação para arquivos Python, JavaScript e TypeScript usando OpenAI, Gemini ou Ollama.

Por padrão, ele apenas mostra um diff. Para gravar, revise a prévia e execute:

```shell
doc-gub --output apply
```

Em automações, use `--yes` junto com `--output apply`. O arquivo é comparado com a prévia imediatamente antes da escrita; se tiver sido alterado, o `doc-gub` não o sobrescreve.

## Uso

```shell
doc-gub                         # mudanças Git (staging tem prioridade)
doc-gub src/                    # arquivo ou diretório específico
doc-gub --selection repository  # todos os arquivos elegíveis
doc-gub --coverage all --format numpy
doc-gub config init
doc-gub config show
```

A configuração segue esta precedência: flags, `--config`, variáveis `DOC_GUB_*`, `.doc-gub.toml`, configuração do usuário e valores padrão. Credenciais usam `OPENAI_API_KEY` ou `GEMINI_API_KEY`; o Ollama não requer credencial.
