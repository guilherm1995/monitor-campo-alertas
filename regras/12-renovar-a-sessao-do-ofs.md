# 12 -- Renovar a sessão do OFS

Como qualquer operador renova a sessão do OFS, sem Python, sem Playwright e sem
o administrador. Implementado em `site/web/ofs_sessao.py` (o recebedor), duas
rotas em `site/web/app.py`, e a extensão em `extensao_ofs/`.

---

## O que era, e por que precisou mudar

O OFS pede código (MFA) no login. Isso não tem volta: a renovação da sessão é
sempre humana, num navegador. O que dava para mudar era **quem** e **de onde**.

Até 01/09/2026, era uma pessoa só, numa máquina só:

    cd C:\caminho\para\Documents\migracao_linux\bot ; python ofs_extracao.py login

Aquilo abre um Playwright, espera o login, guarda os cookies e os copia para o
servidor. Funciona -- e depende de uma pessoa, uma máquina e uma cópia do
código. No dia em que essa pessoa está de férias, a extração do OFS para: o
painel congela, a prévia da carga responde com número velho, e a confirmação de
agenda liga para a lista de ontem.

## Como é agora

O operador **já entra no OFS todo dia**, no Chrome dele. A sessão de que
precisamos é exatamente essa.

1. ele abre o OFS e entra normalmente (o MFA que ele já faz é o único que existe);
2. clica no ícone da extensão **OPERACIONAL — Renovar sessão do OFS**;
3. a extensão lê os cookies daquela sessão e manda para `painel.example.com`;
4. o site grava em `/opt/operacional/bot/dados/ofs_cookies.json`, onde o bot procura.

Nada de Python na máquina do operador, nada de Playwright, nada de um segundo
login.

### Por que uma extensão, e não um `.exe`

Um executável teria de trazer Python, Playwright e um Chromium inteiro -- uns
300 MB, briga com antivírus -- **e ainda assim** abriria um segundo navegador
para o operador logar de novo, com MFA e tudo.

Ler o cookie direto do disco do Chrome é pior: ele criptografa com DPAPI e
mantém o banco travado enquanto está aberto.

A extensão aproveita a sessão que já existe. E `chrome.cookies` enxerga os
cookies **HttpOnly** -- os de sessão, justamente os que o JavaScript de uma
página nunca vê. É por isso que isto precisa ser extensão, e não um script.

---

## A autenticação: dois portões, e nenhum substitui o outro

Um pedido da extensão atravessa **duas** conferências antes de gravar qualquer
coisa. Elas são de camadas diferentes e existem por razões diferentes.

### 1. O Cloudflare Access, na borda

`painel.example.com` inteiro fica atrás do Cloudflare Access: toda requisição é
desviada para um login antes de tocar no site. Uma extensão não consegue fazer
esse login -- ela receberia a tela da Cloudflare em vez da rota.

A saída **não** foi desligar o Access naquele caminho. Existe uma opção assim
(política *Bypass*), e ela deixaria o endpoint alcançável pela internet com o
portão da borda removido. O que está configurado é uma política
**Autorizar serviço** (Service Auth):

| | |
|---|---|
| aplicação | `OPERACIONAL - renovar sessão do OFS` |
| destino | `painel.example.com/ofs/sessao` |
| política | `Extensão renova sessão do OFS`, ação *Autorizar serviço* |
| token de serviço | `extensao-ofs` |
| **vence em** | **01/09/2027** |

A extensão manda `CF-Access-Client-Id` e `CF-Access-Client-Secret`. Sem eles a
Cloudflare responde **403** e o pedido nem chega ao nosso servidor -- medido em
01/09/2026, inclusive com o token do servidor correto.

O resto do site não mudou: `/painel` e as outras telas seguem no login normal
do Access (302), porque o Access resolve pelo caminho MAIS específico, e a
aplicação nova só cobre `/ofs/sessao`.

**O vencimento em 01/09/2027 é a armadilha desta montagem.** Um ano é escolha
consciente -- "sem expiração" seria pior higiene --, mas vencimento quebra
calado numa data que ninguém lembra: a extensão passa a dizer "a Cloudflare
recusou o acesso" e a extração do OFS envelhece. A Cloudflare avisa antes; a
data está escrita aqui e no `credenciais.js` para o aviso não ser a única
defesa.

#### Conferindo pela linha de comando: cuidado com o erro 1010

`curl` e scripts levam **403 com `error code: 1010`** neste domínio, mesmo com
todas as credenciais certas. Isso não é o Access: é a verificação de integridade
de navegador da Cloudflare barrando pelo User-Agent. O Chrome real nunca vê isso.

Quem for conferir de fora precisa mandar um User-Agent de navegador. A conferência
completa, feita em 01/09/2026, foi esta -- e as quatro linhas juntas são o que
prova que os portões são independentes:

    sem cabeçalho nenhum   403   o Access barra
    só o token do servidor 403   o Access barra primeiro
    só os da Cloudflare    401   passa a borda, e o SITE recusa
    com os três            200   {"existe":true,"cookies":4,...}

Sem a terceira linha, um 200 no fim não provaria nada: poderia ser uma camada
só funcionando e a outra aberta.

### 2. O nosso token, no site

Passado o portão da borda, o site confere `Authorization: Bearer <token>`
sozinho, contra `/opt/operacional/ofs_renovacao.token`. Sem ele: **401**.

Isso não é redundância à toa. Se o Access for desligado por engano algum dia --
uma política editada, uma aplicação apagada --, o segundo portão ainda recusa
quem não tem o código. Uma camada que só existe enquanto a outra funciona não é
camada.

### E o CSRF?

A conferência de origem do `guarda` existe para proteger rota que confia em
COOKIE: um formulário em outro site consegue fazer o navegador anexar o nosso
cookie, e o `Origin` é o que denuncia isso. Rota autenticada por token não tem
esse problema, porque não há cookie para anexar.

Por isso `/ofs/sessao` é a **única** rota isenta da conferência de origem
(`SEM_CONFERIR_ORIGEM`, em `app.py`), e a isenção é o desenho certo, não uma
exceção aberta na pressa. Qualquer rota nova naquela lista tem de se autenticar
sozinha.

### O que o token NÃO é

Não é senha de ninguém, e não abre o site. Quem o tiver consegue apenas
**entregar uma sessão do OFS**. Não lê dado, não apaga nada, não entra em tela
nenhuma.

### Onde ele mora, e como se troca

Num arquivo fora do repositório, que só o serviço lê:

    /opt/operacional/ofs_renovacao.token

Para gerar (ou trocar) -- o valor nasce no servidor e ninguém precisa inventá-lo:

```bash
sudo python3 -c "import secrets,pathlib; p=pathlib.Path('/opt/operacional/ofs_renovacao.token'); p.write_text(secrets.token_urlsafe(32)); p.chmod(0o600)" && sudo chown operacional:operacional /opt/operacional/ofs_renovacao.token && sudo systemctl restart operacional-site
```

Depois de trocar, o valor novo entra no `credenciais.js` e a pasta é redistribuída -- ou, para uma máquina só, em **Ajustar**.

**Sem token instalado, a rota fica FECHADA** -- ela responde 401 a todo mundo.
É o contrário do padrão cômodo (aceitar tudo quando não há segredo
configurado), e é de propósito: um endpoint que aceita sessão de quem quiser,
ligado por esquecimento, é pior do que um que não existe.

---

## O que o servidor recusa, e por quê

O modo de falhar que assusta aqui não é o ataque: é o **engano**. Gravar por
cima de uma sessão viva uma sessão vazia não levanta erro nenhum. A extração
seguinte responde "sessão vencida", e quem for olhar vai procurar o problema no
OFS -- não no arquivo que acabou de ser sobrescrito.

Por isso são recusados, com 422 e um texto que diz o que fazer:

| o que chegou | por que |
|---|---|
| menos de 3 cookies | o operador clicou sem estar logado no OFS |
| cookies de outro domínio | extensão mal configurada, ou aba errada |
| corpo que não é objeto JSON | não veio da nossa extensão |

E a gravação é **atômica**: escreve num arquivo ao lado e renomeia por cima. Sem
isso, uma extração que estivesse lendo no exato momento da troca leria meia
sessão -- e o erro apareceria como "sessão vencida" numa sessão recém-nascida.

O `teste_ofs_sessao.py` cobre os dois lados disso, e uma coisa a mais que vale
por todas: ele grava pelo site e **lê de volta com o `ofs_extracao.carregar_cookies`
do bot**. Se os dois formatos divergirem, é ali que aparece -- e não no dia em
que a extração parar.

---

## Quem renova precisa enxergar as DUAS áreas

A extração pede `OPERACIONAL CGT` e `OPERACIONAL RJ` por `providerId` fixo -- não pelo
que a pessoa logada vê na tela. Enquanto a renovação era de uma pessoa só, isso
não era pergunta. Com qualquer operador renovando, passou a ser.

Se a conta não tiver acesso a uma das áreas, o OFS devolve aquela área com o
**cabeçalho e zero linhas**. Essa resposta passava no `validar_csv` -- ela não
está vazia e começa com o cabeçalho certo. A base seria refeita com metade da
operação faltando: o backlog do Rio sumiria, e o painel mostraria um número
menor com cara de número certo.

O `conferir_areas` fecha isso. Ele soma as linhas por área na **janela inteira**
e recusa a gravação se alguma vier com zero, dizendo o motivo provável:

    a extração voltou sem NENHUMA linha em: OPERACIONAL RJ. Isso costuma ser uma
    sessão de um usuário do OFS que não enxerga essa(s) área(s) -- quem renovou
    por último não tem acesso a tudo. Peça a renovação a quem enxerga as duas
    áreas. Nada foi gravado.

A conferência é sobre a janela, e não por dia, de propósito: um dia isolado sem
atividade numa área acontece (feriado, domingo), e recusar ali derrubaria a
extração por um motivo legítimo.

Em 01/09/2026 a operação conferiu que **todos os logins do OFS que temos hoje
enxergam as duas áreas**. A trava fica mesmo assim: "hoje todos enxergam" é
verdade de hoje, e o que ela impede é uma falha que sai calada -- um operador
novo, uma permissão mexida do lado do provedor.

---

## Quem é avisado quando a sessão cai

O aviso sempre saiu no grupo de comandos, junto de todo o resto. Desde
01/09/2026 ele sai **também no privado** de quem pode renovar.

A razão é simples: quem tem de agir pode estar sem olhar o grupo, que recebe
dezenas de mensagens por dia. No privado a mensagem não divide espaço com nada.

### Como o bot sabe o JID de cada um

Ele **aprende**, e isso não é preciosismo. O WhatsApp entrega conversa privada
com um identificador `@lid` opaco -- `34132138688546@lid` -- que NÃO é o
telefone e não se deduz dele. `BOT_PV_LIBERADOS` guarda só os dígitos, e montar
o JID grudando um sufixo é exatamente o erro que já aconteceu aqui: o `@lid`
foi tratado na entrada e esquecido na saída, e o envio respondeu HTTP 500 sem
ninguém perceber.

Então: toda vez que alguém liberado escreve no privado, o JID **inteiro** que o
WhatsApp mandou é guardado em `dados/pv_conhecidos.json`
(`ofs_extracao.lembrar_pv`). Nada é construído, e no dia em que o WhatsApp
mudar o formato de novo isso continua funcionando.

Consequência prática: **quem nunca escreveu para o bot no privado não recebe o
aviso.** Uma mensagem qualquer basta para se registrar.

### Quem recebe hoje

Só a OPERADOR, em `34132138688546@lid`. Ela é quem renova na prática; o
administrador continua sabendo pelo grupo, que recebe o mesmo aviso com o
procedimento inteiro.

Isso está na variável `OFS_AVISO_PV`, e ela **manda quando está preenchida**:
os JIDs dela são os únicos que recebem. Quem escreve a lista à mão está
escolhendo os destinatários, e somar os aprendidos por cima faria a mensagem
chegar a quem foi deliberadamente deixado de fora.

Sem a variável, valem todos os aprendidos -- todo mundo que já escreveu no
privado.

### O texto é da operação

As duas mensagens do privado (`AVISO_PV_CAIU` e `AVISO_PV_VOLTOU`, em
`painel_resultados.py`) foram escritas pela operação, e são deliberadamente
diferentes das do grupo:

    Oi OPERADOR!  😭

    A seção do OFS caiu 🥹 preciso que você use a extensão para renova a seção 😭😭😭

    ---

    obrigaduuuuuuu OPERADOR 💙🩵 agora os relatórios automáticos voltaram 🤖
    "abraço robótico" 🫂

No grupo o aviso divide espaço com dezenas de mensagens por dia e precisa do
procedimento inteiro junto. No privado ele é para UMA pessoa que vai agir
agora, e o que funciona ali é curto e humano.

**Não mexer nesses textos sem falar com a operação**: quem escreveu conhece
quem lê.

### O agradecimento só vai para quem soube da queda

O `_avisar_volta` só manda quando o estado era de falha. Sem esse `if`, todo
ciclo bem-sucedido -- de hora em hora, o dia inteiro -- mandaria um "voltou"
para quem nunca viu nada quebrar. Agradecimento que chega sem problema antes é
ruído, e ruído gasta a atenção que a próxima queda vai precisar.

### Uma vez, não de hora em hora

O ciclo do painel roda de hora em hora, e enquanto a sessão seguir vencida ele
tornaria a avisar. O `_avisar_falha` já represa isso no grupo (a primeira vez,
depois a cada `INTERVALO_AVISO_SEG`), e o privado **só sai quando o aviso do
grupo saiu de verdade** -- ele lê o retorno daquela função.

Sem essa amarra o privado receberia a mesma mensagem toda hora, e lembrete de
hora em hora vira paisagem: é exatamente o que o represamento do grupo existe
para evitar. O `teste_aviso_sessao.py` cobre os quatro casos: a primeira vez,
a repetição cedo demais, a repetição depois da janela, e nenhum privado
conhecido (aí o grupo ainda recebe).

O aviso sai do ciclo do **painel de resultados**, que roda de hora em hora. O
`ofs_base_historica` também percebe a sessão vencida, mas avisa uma vez por dia
e só no grupo -- o painel sempre chega primeiro, e duplicar seria barulho.

---

## O que o operador vê

A extensão mostra, **antes** de qualquer clique, de quando é a sessão que está
no servidor. Sem isso ele não tem como saber se precisa renovar, e "na dúvida,
clica" transforma um botão de manutenção em hábito.

Depois de renovar, ela mostra a idade de novo. Essa é a única confirmação que
não mente: "deu certo" sem prova é a mesma coisa que nada.

## As credenciais da extensão

São três, e todas ficam em `extensao_ofs/credenciais.js`:

    cfClientId       Cloudflare, do token de serviço `extensao-ofs`
    cfClientSecret   Cloudflare, mostrado UMA VEZ na criação do token
    token            o nosso, de /opt/operacional/ofs_renovacao.token

**Esse arquivo não vai para o repositório** (`.gitignore`). Versioná-lo
colocaria dois segredos no histórico do Git para sempre -- e histórico de Git
não se apaga, se reescreve. O que fica versionado é o `credenciais.exemplo.js`,
com os campos vazios, para quem for montar outra instalação saber o que
preencher.

Com a pasta preenchida, **ninguém digita nada em máquina nenhuma**: é só copiar
e carregar. A tela **Ajustar** continua existindo para trocar uma credencial
numa máquina sem redistribuir a pasta, e o que for preenchido ali manda sobre o
que veio no arquivo.

O `cfClientSecret` aparece uma vez só. Se ele se perder, não há recuperação:
gera-se outro token de serviço, atualiza-se o `credenciais.js`, e a política do
Access passa a apontar para o token novo.

## A pasta que vai para o Chrome

Ela leva **só** o que o Chrome entende. Nada de script Python lá dentro: rodar
um cria `__pycache__`, e o Chrome recusa a extensão inteira com

    Cannot load extension with file or directory name __pycache__.
    Filenames starting with "_" are reserved for use by the system.

Aconteceu na primeira instalação. Por isso o `preencher_credenciais_ofs.py` mora
**ao lado** da pasta, não dentro dela.

## Instalar a extensão numa máquina

1. copie a pasta `extensao_ofs` **já preenchida** para a máquina;
2. no Chrome: `chrome://extensions` → ligue **Modo do desenvolvedor** →
   **Carregar sem compactação** → escolha a pasta;
3. clique no ícone → **Ajustar** → escreva só o seu nome (o resto já vem);
4. abra o OFS, entre, e clique em **Renovar agora**.

O nome do passo 3 não é enfeite: ele vai no `renovado_por` do arquivo de
sessão, e é como se sabe quem renovou por último quando algo estranho aparecer.
