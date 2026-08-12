# Monitor de Campo — vigilância e alertas de ordens de serviço

> Monitora um sistema web de field service atrás de VPN, detecta situações que exigem ação e alerta por Telegram e WhatsApp. Roda sem operador, 24/7, num painel de TV na sala de operação.

![status](https://img.shields.io/badge/status-portfolio-blue)
![licença](https://img.shields.io/badge/licen%C3%A7a-MIT-green)

## O que faz

Um sistema de ordens de serviço só avisa quando alguém olha. Este monitor olha
por você: mantém uma sessão viva no portal, varre a lista de chamados em ciclo,
e dispara alerta quando encontra algo que precisa de ação — reparo em garantia,
CAPEX entrante, chamado improdutivo, backlog acima do limite.

O que ele faz além do óbvio é **se manter de pé**. Boa parte do código não é
sobre chamados, é sobre sobreviver a uma VPN que cai, um navegador que morre,
e uma máquina que precisa rodar meses sem ninguém por perto.

## Arquitetura

```
bot_campo_monitoramento.py   processo principal: scraping, painel de TV, orquestração
backlog_*.py                 geradores de relatório por tipo (capex, ofs, reparo, ...)
backlog_render.py            renderizador PNG, roda em subprocesso isolado
improdutivas.py              análise de chamados sem produtividade
termometro_render.py         termômetro de CAPEX entrante
vpn_sempre_ativa.py          serviço que mantém a VPN levantada
index.js                     sidecar Node: envia alerta para grupo de WhatsApp
deploy/linux/                porte para Linux: systemd, watchdog de VPN, TV em kiosk
```

## As decisões que sustentam o resto

**Render em subprocesso.** Gerar PNG dentro do processo principal vazava memória
e sujava a contagem de erros. O renderizador virou um desvio no topo do próprio
executável (`--render-backlog`), antes dos imports pesados: o filho não abre o
log do pai nem contamina as estatísticas.

**Watchdog em vez de try/except.** O processo registra uma batida periódica. Uma
thread separada verifica a idade dessa batida e, se o principal travou, relança.
Erro que você não previu não vira exceção tratada — vira processo reiniciado.

**Reconexão de VPN dirigida pelo estado da janela.** O cliente de VPN é um app
gráfico sem CLI útil. A reconexão inspeciona as janelas do Windows (inclusive as
*cloaked*, que existem mas não aparecem), fecha popup de erro fatal, conta
crashes e aplica espera progressiva antes de tentar de novo.

**Lock de instância única.** Duas cópias rodando dobrariam os alertas no grupo.
O lock é por arquivo, e funciona igual no Windows (`msvcrt`) e no Linux (`fcntl`).

**Reciclagem preventiva.** O Chromium fragmenta memória em execução longa. O
processo se recicla por tempo e por RAM livre da máquina, em janela de baixa
atividade, em vez de esperar o travamento.

## O painel de TV

A tela da sala é Tkinter puro, em 1366×768, ampliado para 1080p pela TV. Sem
navegador e sem framework: é o mesmo processo que faz o scraping, o que evita
uma segunda aplicação para manter viva. Em troca vêm limitações reais — o
Canvas do Tk não tem canal alfa, não arredonda canto e não suaviza forma
nenhuma — e o desenho é construído em cima delas.

**Formas desenhadas fora do Tk.** Cada card é uma imagem gerada com Pillow em
3× e reduzida com LANCZOS; é daí que vem o canto arredondado limpo. Nenhum
traço de 1px é desenhado pelo Tk: numa tela que ainda vai ser ampliada, régua
fina sai dura e tremida, então a separação entre superfícies é por contraste
de cor, não por borda.

**Fade sem alfa.** A entrada de um item interpola todas as cores a partir da
cor de fundo — texto, borda e acento — o que dá o efeito de materializar sem
precisar de transparência. Os quadros do fade são pré-gerados no arranque, uma
imagem por tique do laço, para o primeiro evento do dia não pagar a conta de
renderizar tudo no meio da animação.

**A chegada tem peso.** O card abre em ease-out empurrando a lista, e o
conteúdo entra deslizando e assenta com uma ultrapassagem curta
(ease-out-back). A ultrapassagem fica só no conteúdo, nunca na altura: altura
que passa do ponto faz a lista inteira dar um solavanco.

**Altura derivada do espaço real.** O card é dimensionado pelo número máximo de
itens da coluna, não pelos que estão em tela. Assim a lista cheia ocupa a tela
exata, e nenhum card muda de tamanho quando chega um item novo —
redimensionar todo mundo a cada evento atropelaria a animação de chegada.

**Consulta lenta fora do laço gráfico.** O status de conexão de cada cliente
vem de uma consulta que leva dezenas de segundos e depende da VPN. Ela roda em
thread própria e entrega por fila; o laço do Tk só consome. Duas falhas
seguidas viram "SEM DADOS" em vez de deixar na tela um status velho, que
engana quem olha de longe.

**Alerta que dá para ler.** A versão anterior pintava a tela inteira de
vermelho e piscava ligando e desligando a cor: chamava atenção e não deixava
ler nada. Hoje pulsa só a moldura, por interpolação — de longe chama igual, e
de perto o miolo continua escuro, com os dados em blocos grandes e uma barra
mostrando quanto falta para o alerta sair sozinho.

**Fonte resolvida em tempo de execução.** O código pedia uma fonte que só
existe no Windows; no Linux o Tk caía num substituto silencioso. Agora a
família é escolhida entre as que a máquina realmente tem, e os tamanhos são
declarados em pixel — em ponto, o mesmo número muda de tamanho conforme o DPI
da tela.

## Porte para Linux

A pasta `deploy/linux/` tem o que faz isso rodar como serviço de verdade:

- units systemd para o bot, a VPN, o sidecar de WhatsApp e o site
- watchdog de VPN em `timer` — a VPN é rota padrão, então um túnel morto derruba
  tudo; o watchdog é externo ao serviço que ele vigia, de propósito
- display HDMI em kiosk para o painel de TV, com regra udev para religar a saída
  quando a TV volta à energia

## Rodando

```bash
pip install -r requirements.txt
npm install
playwright install chromium
cp .env.example .env      # preencha antes de rodar
python bot_campo_monitoramento.py
```

Para testar sem disparar no grupo real, aponte `TELEGRAM_CHAT_ID` para um grupo
de teste seu. Variável vazia não desliga o envio — ela cai no valor padrão.

## Aviso

Este repositório é uma versão de portfólio, extraída de um sistema que rodou em
produção. Foi anonimizado antes da publicação: nomes de empresas, domínios
internos, credenciais, sessões de mensageria e dados de clientes foram
substituídos por valores de exemplo. Os arquivos de configuração são gabaritos,
não os valores reais de operação.

O código está aqui como referência técnica. Para rodar de verdade, é preciso
apontar as variáveis de ambiente e os configs para um ambiente próprio.

## Licença

MIT — veja [LICENSE](LICENSE). Copyright (c) 2026 Guilherme da Silva dos Santos.
