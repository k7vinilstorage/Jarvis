# Fase 1: o cérebro em texto

**Objetivo:** o Jarvis responder rápido e certo pela API, com as ferramentas do dia a dia: hora, clima, Moodle, Google Agenda, Gmail e busca na web.

**Portão para a Fase 2:** acertar 8 das suas 10 perguntas no `scripts/testar-jarvis.py` (passo 3).

## Onde estamos

| Medida | Fase 0 | Passo 1 | Passo 2 (hora e clima) |
|---|---|---|---|
| Prompt por pergunta | 13.302 tokens | 3.900 | 2.855 |
| Modelo frio, até o 1º token | 12,5 s | 7,6 s | 5,9 s |
| Pergunta simples, até a 1ª palavra | 0,9 s | 1,0 s | 0,2 s |
| "Que horas são?" | 3,6 s, 3 chamadas | 3,2 s, 3 chamadas | 1,0 s, 1 chamada |
| Raciocínio | ligado | ligado (valor errado) | desligado |

O raciocínio só desligou com `agent.reasoning_effort false`. O `hermes config set ... none` grava o valor como vazio, e o Hermes volta ao padrão.

---

## Passo 1: enxugar o prompt (feito)

O `scripts/fase1-enxugar.sh` deixou a API só com as ferramentas necessárias, desligou o Tool Search e o raciocínio, tirou as orientações feitas para modelos grandes e trocou o `SOUL.md`. Tudo isso vale só para a API; o `hermes chat` no terminal continua com tudo. Para voltar: `./scripts/fase1-enxugar.sh --desfazer`.

---

## Passo 2: ferramentas próprias (`jarvis-tools`)

O `jarvis-tools` é um contêiner com um servidor MCP. O Hermes fala com ele pela rede interna do Docker, com um token, e nenhuma porta é publicada. As senhas e tokens do Moodle e do Google ficam só em `data/jarvis-tools`; o Hermes e o modelo nunca os veem.

| Ferramenta | O que faz | Precisa de |
|---|---|---|
| `hora` | data e hora de Brasília | nada |
| `clima` | previsão do Open-Meteo; cidade padrão `JARVIS_CIDADE`, ou outra dita na pergunta | nada |
| `moodle_prazos` | atividades pendentes, uma por linha com a disciplina: hoje, amanhã, semana, mês, uma data ou atrasadas; de todas ou de uma disciplina | login no Moodle |
| `moodle_provas` | provas e questionários dos próximos 30 dias, de todas ou de uma disciplina | login no Moodle |
| `moodle_disciplinas` | disciplinas em andamento | login no Moodle |
| `moodle_conteudo` | abre uma disciplina: professores, avisos, prazos e materiais por seção; com um assunto, procura dentro dela (páginas, descrições, avisos) | login no Moodle |
| `moodle_atividade` | uma atividade: descrição do professor, prazo, se você já entregou, nota, tentativas | login no Moodle |
| `agenda` | compromissos de uma ou de todas as contas Google | login no Google |
| `agenda_criar` | cria evento, só depois de você confirmar | login no Google |
| `emails` | os e-mails mais novos (ou de um período, ou de um remetente/assunto), cada um com um id | login no Google |
| `ler_email` | lê um e-mail inteiro, pelo id da lista ou por uma busca ("fatura da copel") | login no Google |
| `buscar` | pesquisa pelo seu SearXNG, abre as 3 melhores páginas e devolve os trechos que respondem, com o site | SearXNG |
| `ler_pagina` | lê mais de uma página que apareceu numa busca | SearXNG |

Uma ferramenta só aparece para o Jarvis quando está configurada. Sem Moodle, por exemplo, ele nem vê as do Moodle.

A disciplina pode ser dita do jeito que você fala: "TCC", "a matéria de TCC", "trabalho de conclusão", "sistemas distribuídos", "dispositivos". O `jarvis-tools` limpa os nomes do Moodle (tira códigos como `DACOM-CP`, turma e semestre), gera as siglas e acha a disciplina certa. Se duas combinarem ("sistemas"), ele pergunta qual. Para conferir como ele entende as suas disciplinas:

```bash
docker compose exec -T jarvis-tools python -m app.cli moodle_nomes
```

Se algum nome falado sair estranho, me mande essa saída: ela só tem os nomes das disciplinas.

### Instalar ou atualizar

```bash
cd /opt/jarvis
./scripts/fase1-jarvis-tools.sh
```

O script faz o seguinte:
1. **Configuração:** completa o `.env`, incluindo `MOODLE_URL`, e cria a pasta `data/jarvis-tools`.
2. **SearXNG:** se achar o contêiner `searxng`, liga o `jarvis-tools` à rede dele.
3. **Ferramentas:** recompila e sobe o `jarvis-tools`.
4. **Hermes:** ajusta a configuração e reinicia.
5. **Fim:** mostra o que ficou ligado e os próximos passos.

### Moodle

```bash
docker compose exec -it jarvis-tools python -m app.moodle_login
docker compose restart jarvis-tools hermes
```

O comando pede seu usuário e senha do Moodle (a senha não aparece enquanto você digita) e troca por um token do mesmo serviço que o app do celular usa. **A senha não fica guardada**, só o token, em `data/jarvis-tools/moodle.json`, com permissão só sua. Para desligar: `python -m app.moodle_login --remover`. Para invalidar o token no próprio Moodle, apague a chave do aplicativo em Preferências > Chaves de segurança.

As provas vêm do calendário dos seus cursos nos próximos 30 dias. O Jarvis considera prova qualquer evento com prova, avaliação, exame, teste, recuperação, P1, P2... no nome, mais os questionários.

### Google Agenda e Gmail (uma ou várias contas)

**Uma vez, no Google Cloud** (grátis), com a conta que vai ser dona do projeto:
1. Em [console.cloud.google.com](https://console.cloud.google.com), crie um projeto, por exemplo "Jarvis".
2. **APIs e serviços > Biblioteca:** ative a **Google Calendar API** e a **Gmail API**.
3. **Tela de consentimento OAuth** (em "Plataforma de autenticação do Google"):
   - Tipo: **Externo**.
   - Nome do app: Jarvis. Coloque seu e-mail como suporte e contato.
   - **Páginas do app:** para publicar, o Google exige uma página inicial e uma política de privacidade num domínio seu. As duas páginas prontas estão em `docs/google-paginas/`. Publique essas páginas no GitHub Pages, conforme o quadro abaixo. Em **Branding**, preencha a página inicial, o link da política de privacidade e o domínio autorizado (`USUARIO.github.io`), e salve. Não envie logotipo, porque isso obriga a uma verificação do Google.
   - Em **Público-alvo**, clique em **Publicar app**, para ele ficar **Em produção**. Isso é importante: se ficar "Testando", o Google derruba a autorização a cada 7 dias. O aviso de que o app precisa de verificação pode ser ignorado, porque o app é só seu.

   > **GitHub Pages (grátis):** crie um repositório público, por exemplo `jarvis-app`. Em **Add file > Create new file**, crie o `index.html` e o `privacidade.html` com o conteúdo de `docs/google-paginas/`. Depois, em **Settings > Pages**, escolha **Deploy from a branch**, depois `main` e `/ (root)`, e salve. Em um ou dois minutos as páginas ficam em `https://USUARIO.github.io/jarvis-app/` e `https://USUARIO.github.io/jarvis-app/privacidade.html`. Troque `USUARIO` pelo seu usuário do GitHub.
   >
   > **Para testar antes de publicar:** em **Público-alvo > Usuários de teste**, adicione cada conta Google que você vai ligar (a segunda também). O login funciona na hora, mas vence em 7 dias. Depois de publicar, rode o `google_login` de novo, uma vez, com o mesmo rótulo.
4. **Credenciais > Criar credenciais > ID do cliente OAuth**:
   - Tipo: **App para computador**.
   - Baixe o JSON.
5. Copie o JSON para o servidor como `data/jarvis-tools/google/cliente.json`. Pelo seu PC, por exemplo:
   ```bash
   scp client_secret_*.json USUARIO@IP-DO-SERVIDOR:/opt/jarvis/data/jarvis-tools/google/cliente.json
   ```

**Para cada conta Google que você quer ligar:**

```bash
docker compose exec -it jarvis-tools python -m app.google_login pessoal
```

Use um rótulo curto por conta, como `pessoal` ou `faculdade`.

1. O comando mostra um endereço. Abra no navegador do seu PC e entre com a conta que você quer ligar a esse rótulo. Para a segunda conta, use uma janela anônima: assim o Google não entra direto com a conta já logada.
2. Vai aparecer o aviso "O Google não verificou este app". Isso é normal, porque o app é seu e o Gmail é uma permissão "restrita". Clique em **Avançado > Acessar Jarvis** e marque todas as caixas.
3. Depois de aprovar, o navegador mostra uma página de erro ("não foi possível conectar"). Isso é esperado.
4. Copie o endereço inteiro da barra, que começa com `http://127.0.0.1:8765/`, e cole no terminal.

Repita com outro rótulo para cada conta. No fim, rode `docker compose restart jarvis-tools hermes`.

- **Mais de uma conta:** agenda e e-mails juntam todas as contas e dizem de qual conta veio cada item. Para uma conta só, diga na pergunta, como "o que tenho na agenda da faculdade?".
- **Conta usada para criar eventos:** `GOOGLE_CONTA_PADRAO` no `.env`, ou a primeira em ordem alfabética.
- **Listar e remover:** `python -m app.google_login --listar` e `python -m app.google_login --remover pessoal`. A remoção também revoga o acesso no Google.
- **Contas da faculdade:** contas institucionais (Google Workspace) podem bloquear apps não verificados. Se o login da conta da faculdade falhar com "acesso bloqueado", fique só com a pessoal.

### Busca na web

O script liga a busca sozinho se achar o seu contêiner `searxng` numa rede do Docker. Se ele tem outro nome: `SEARXNG_CONTAINER=nome ./scripts/fase1-jarvis-tools.sh`.

O SearXNG precisa responder em JSON. Se a busca disser que o formato JSON foi recusado, acrescente `json` em `search.formats` no `settings.yml` dele e reinicie. A Open WebUI também precisa disso, então provavelmente já está ativo.

**Privacidade:** o `buscar` recusa consultas com o seu nome ou e-mail, que iriam para os buscadores de fora pelo SearXNG. Os e-mails das contas Google entram sozinhos; o nome, o sobrenome e os apelidos você põe em `JARVIS_TERMOS_PRIVADOS` no `.env`, separados por vírgula, e aplica com `./scripts/fase1-jarvis-tools.sh`. A comparação é por palavra inteira, então um nome comum sozinho bloquearia buscas como "clima em João Pessoa": prefira o nome completo e o sobrenome.

Por segurança, o `ler_pagina` só lê páginas que apareceram numa busca feita nos últimos 30 minutos. Ele nunca acessa a rede da sua casa nem a do Docker. Assim, um e-mail ou uma página com texto malicioso não consegue mandar o Jarvis visitar um endereço inventado levando dados seus.

### Testar as ferramentas sem o modelo

```bash
docker compose exec jarvis-tools python -m app.cli                      # lista o que existe
docker compose exec jarvis-tools python -m app.cli recursos             # o que está configurado
docker compose exec jarvis-tools python -m app.cli clima "Londrina, PR" amanhã
docker compose exec jarvis-tools python -m app.cli moodle_prazos semana
docker compose exec jarvis-tools python -m app.cli moodle_provas
docker compose exec jarvis-tools python -m app.cli moodle_conteudo tcc
docker compose exec jarvis-tools python -m app.cli moodle_conteudo tcc "data da defesa"
docker compose exec jarvis-tools python -m app.cli moodle_atividade "lista 2" "sistemas distribuídos"
docker compose exec jarvis-tools python -m app.cli agenda hoje
docker compose exec jarvis-tools python -m app.cli emails                        # os mais novos
docker compose exec jarvis-tools python -m app.cli emails recentes "" copel      # de um remetente/assunto
docker compose exec jarvis-tools python -m app.cli ler_email "fatura da copel"
docker compose exec jarvis-tools python -m app.cli buscar "ubuntu 26.04"
docker compose logs -f jarvis-tools                                      # uma linha por chamada do Jarvis
```

Se a ferramenta responde certo aqui e o Jarvis erra, o problema está no modelo, não na integração.

Os testes automáticos, sem internet, rodam com `docker compose run --rm --no-deps jarvis-tools python -m unittest discover -s testes`.

---

## Passo 3: testar na prática

### Conversar

```bash
python3 scripts/chat.py
```

É um chat no terminal, com histórico. Depois de cada resposta, ele mostra as ferramentas usadas e o tempo. Comandos: `/nova` (conversa nova), `/historico`, `/tempo`, `/sair`.

### O vetor de testes

```bash
python3 scripts/testar-jarvis.py                     # os casos que só leem
python3 scripts/testar-jarvis.py --listar            # a lista de casos
python3 scripts/testar-jarvis.py --categoria moodle  # só um grupo (ou --caso ID)
python3 scripts/testar-jarvis.py --repetir 3         # consistência: o caso precisa passar as 3 vezes
python3 scripts/testar-jarvis.py --incluir-escrita   # também grava memória e cria um evento de teste
```

Os casos ficam em `testes/jarvis-casos.json`. Cada caso diz:
- que ferramenta o Jarvis deve usar e qual não pode usar;
- o que a resposta precisa conter;
- o tempo máximo até a primeira palavra: 3 s, ou 10 s quando usa ferramenta (20 s nas buscas, que abrem as páginas).

Casos de Moodle, Google ou busca são pulados enquanto a integração não estiver configurada. O relatório completo, com todas as respostas e o modelo usado, vai para `medicoes/teste-AAAAMMDD-HHMM-<modelo>.md` (ou use `--rotulo nome`). **Leia as respostas**: o teste confere a forma, mas só você sabe se as provas e os compromissos estão certos.

As suas 10 perguntas, que decidem a Fase 2:

| # | Pergunta | Caso | Precisa de |
|---|---|---|---|
| 1 | Como está a previsão do tempo hoje? (com e sem cidade) | `clima-hoje`, `clima-londrina` | nada |
| 2 | Como está minha agenda hoje? | `agenda-hoje` | Google |
| 3 | Tenho muitas atividades para essa semana? | `moodle-semana` | Moodle |
| 4 | Quais são minhas próximas provas? | `moodle-provas` | Moodle |
| 5 | Tenho atividades com vencimento para amanhã? | `moodle-amanha` | Moodle |
| 6 | Me fale sobre o funcionamento de um serviço webhook | `webhook` | nada |
| 7 | É possível criar uma skill para...? | `skill-dolar` | nada |
| 8 | Consegue verificar o estado dos meus servidores? | `servidores` | nada: passa se ele admitir que ainda não consegue |
| 9 | O que recebi de importante no meu e-mail? | `email-hoje` | Google |
| 10 | Busque informações sobre... | `busca-ubuntu` | SearXNG |

Há também casos extras: identidade, hora certa, dia certo (pela ferramenta hora), variações do clima, disciplinas, a matéria de TCC, entregas do TCC, detalhes de uma atividade, e-mails mais recentes, ler um e-mail, busca com fonte, agenda de amanhã, formato para voz e recusa de "apague meus e-mails".

---

## Precisão e troca de modelo

Três coisas reduzem as invenções:
1. **Temperatura mais baixa.** O padrão do qwen3.5 é temperatura 1, top_p 0,95 e presence_penalty 1,5, feito para conversa criativa. O `jarvis-qwen` agora usa 0,5 / 0,9 / 20 / 0,3 (`JARVIS_TEMPERATURE`, `JARVIS_TOP_P`, `JARVIS_TOP_K`, `JARVIS_PRESENCE_PENALTY` no `.env`). A penalidade de presença alta atrapalha repetir números e nomes que vieram da ferramenta.
2. **Ferramentas que entregam o texto pronto.** A busca abre as páginas e manda os trechos, os e-mails vêm com id para ler o inteiro, e o Moodle põe disciplina, atividade e prazo na mesma linha.
3. **Regras de precisão no `SOUL.md`:** responder só com o que as ferramentas devolveram, dizer quando não encontrou, usar a ferramenta hora para a data.

Para trocar de modelo ou mexer nos parâmetros, sem tocar na configuração do Hermes:

```bash
./scripts/modelo.sh                            # modelo atual, parâmetros, o que está na GPU, modelos baixados
./scripts/modelo.sh usar qwen3.5:9b            # troca (baixa se precisar) e faz um teste rápido
./scripts/modelo.sh usar qwen3.5:4b --gpu 99   # volta ao 4b forçando 100% GPU
./scripts/modelo.sh voltar                     # volta para o modelo anterior
./scripts/modelo.sh ajustar temperatura 0.3    # ou top_p, top_k, presence_penalty, gpu; "padrao" = o do modelo
./scripts/modelo.sh aplicar                    # recria o jarvis-qwen com o que está no .env
```

O Hermes e o Honcho continuam usando o nome `jarvis-qwen`; o script troca o modelo por trás dele. Ele recusa modelos sem chamada de ferramenta (`tools`) e avisa quando o contexto de treino é menor que os 64 mil que o Hermes exige. Se algo falhar no meio, o `.env` volta ao que era.

Num modelo maior que a VRAM (o `qwen3.5:9b`, por exemplo), parte roda na CPU: `ollama ps` mostra a divisão, e as respostas ficam bem mais lentas. Para comparar, rode `python3 scripts/testar-jarvis.py` com cada modelo: o nome do modelo vai no nome do relatório.

Depois de um `--incluir-escrita`, faça a limpeza:
- apague o evento "dentista" de sexta às 15h na sua agenda;
- rode `python3 scripts/memoria-remover.py Coritiba`.

---

## Passo 4: Honcho

Depois do passo 3, medindo com e sem ele (veja `docs/honcho.md`).

---

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| O Jarvis não usa uma ferramenta nova | Rode `docker compose restart jarvis-tools hermes`: o Hermes só lê a lista de ferramentas ao conectar. Confira com `python -m app.cli recursos`. |
| Moodle: "Usuário ou senha incorretos" | Confira no site. Se a faculdade usa login único (SSO), este método não funciona; me avise. |
| Moodle: "o acesso ao Moodle expirou" | Rode o `moodle_login` de novo |
| Google: "a autorização da conta X expirou" | O app ainda está "Em teste" (veja o passo 3 do Google Cloud) ou você revogou o acesso. Publique o app e rode o `google_login X` de novo. |
| Google: "a API do ... não está ativada" | Ative a Google Calendar API e a Gmail API no projeto |
| Busca: "recusou o formato JSON" | Ative `json` em `search.formats` no SearXNG |
| Busca: erro 429 | O limiter do SearXNG barrou. Desligue `limiter` no `settings.yml` ou espere. |
| `Permission denied` em `data/jarvis-tools` | `sudo chown -R "$(id -u):$(id -g)" data/jarvis-tools` e rode o script de novo |
