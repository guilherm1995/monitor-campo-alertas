// =========================================================
// Serviço standalone: envia alertas para um GRUPO do WhatsApp
// =========================================================
// Este serviço é 100% independente do painel_operacional/index.js.
// Ele usa a mesma lógica (Baileys) para abrir uma conexão própria
// com o WhatsApp (sessão/QR code separados) e expõe um pequeno
// servidor HTTP local para receber pedidos de envio.
//
// Uso típico:
//   1) npm install
//   2) node index.js --listar-grupos   -> loga (QR code) e mostra os
//      grupos disponíveis com seus JIDs, para você copiar o certo
//      para o config.json
//   3) node index.js                   -> roda o serviço normalmente,
//      escutando em http://127.0.0.1:3939/alerta
//
// O bot_campo_monitoramento.py (ou qualquer outro script) manda o alerta
// assim:
//   POST http://127.0.0.1:3939/alerta         body: {"mensagem": "texto..."}
//   POST http://127.0.0.1:3939/alerta-imagem  body: {"imagemBase64": "...", "legenda": "texto opcional"}
//                                              (usado pelo backlog de CAPEX, imagem PNG em base64)
//
// Este serviço também ESCUTA as mensagens que chegam no grupo configurado
// (config.json -> grupoJid) e as guarda numa fila em memória, para que o
// bot_campo_monitoramento.py possa fazer polling (mesmo esquema do getUpdates
// do Telegram) e interpretar comandos como /autenticador direto do WhatsApp:
//   GET http://127.0.0.1:3939/mensagens
//   -> {"ok": true, "mensagens": [{"participante": "operador@provedor.example", "texto": "/autenticador", "timestamp": 1234567890}]}
// Cada chamada a /mensagens devolve e ESVAZIA a fila (consumo único).
// =========================================================

const http = require('http');
const path = require('path');
const fs = require('fs');
const qrcode = require('qrcode-terminal');
const pino = require('pino');
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  downloadMediaMessage,
} = require('@whiskeysockets/baileys');

// Prefixa todo log com o PID deste processo. Serve pra detectar, só olhando
// o log depois, se em algum momento existiram DOIS processos node.exe
// deste serviço rodando ao mesmo tempo (ex: um órfão de uma execução
// anterior que não foi encerrado, brigando pela mesma sessão/auth_alertas
// com o processo novo) -- nesse caso apareceriam PIDs diferentes
// intercalados nas linhas de conexão/desconexão.
const PID_PROCESSO = process.pid;

// Redação de material criptográfico ANTES de qualquer coisa sair no stdout.
//
// Por que isto existe: em 08/08/2026 o monitor_campo.log tinha 38.605 linhas com
// chave de sessão do WhatsApp em texto puro -- `privKey: <Buffer f8 2d 94 ...>`,
// `currentRatchet`, `identityKey` -- de 23/07 até aquele dia. O bot Python lê o
// stdout deste processo e escreve tudo no log dele, então bastava alguém
// imprimir o objeto de sessão para o material ir parar no disco.
//
// A origem é a libsignal, em `src/session_record.js`:
//     console.warn("Session already closed", session);
// que despeja o objeto de sessão INTEIRO. Não adianta corrigir lá: é
// node_modules, e o próximo `npm install` desfaz. O logger do Baileys já está
// em `pino({ level: 'silent' })` e mesmo assim isso passava, porque não vai
// pelo pino -- vai direto no console.
//
// Por isso a redação fica AQUI, no console: pega log, warn e error de uma vez,
// de qualquer biblioteca, hoje e depois de atualizar dependência.
const PADROES_SEGREDO = /(privKey|pubKey|ephemeralKeyPair|currentRatchet|identityKey|signedPreKey|preKey|noiseKey|signedIdentityKey|advSecretKey|myIdentityKey|remoteIdentityKey|chainKey|rootKey|macKey|encKey)/i;
const LIMITE_LINHA = 500;

// Procura material de sessão em profundidade. Olhar só o primeiro nível não
// basta: o objeto da libsignal é `SessionEntry { _chains: { <id>: { chainKey:
// ... } } }`, e o que interessa mora lá embaixo. Um Buffer em QUALQUER nível já
// condena o objeto, porque nenhum log operacional deste serviço precisa
// imprimir bytes crus.
//
// Profundidade 8, e não 4: o teste pegou `{sessions:{id:{record:{_chains:{id:
// {key: Buffer}}}}}}` escapando por um nível. Como o custo é limitado pelos
// cortes de 100 chaves / 50 itens, dá para ser generoso -- e o limite também é
// o que impede referência circular de girar para sempre.
function temSegredo(valor, profundidade) {
  if (profundidade > 8 || valor == null) return false;
  if (Buffer.isBuffer(valor) || ArrayBuffer.isView(valor)) return true;
  if (typeof valor !== 'object') return false;
  if (Array.isArray(valor)) {
    return valor.slice(0, 50).some((v) => temSegredo(v, profundidade + 1));
  }
  let chaves;
  try {
    chaves = Object.keys(valor);
  } catch (e) {
    return false;               // objeto exótico (Proxy hostil, getter que joga)
  }
  for (const k of chaves.slice(0, 100)) {
    if (PADROES_SEGREDO.test(k)) return true;
    let v;
    try {
      v = valor[k];
    } catch (e) {
      continue;                 // getter que joga: ignora esse campo
    }
    if (temSegredo(v, profundidade + 1)) return true;
  }
  return false;
}

function limpar(valor) {
  // Buffer/TypedArray viram resumo: o tamanho é diagnóstico, o conteúdo é chave.
  if (Buffer.isBuffer(valor) || ArrayBuffer.isView(valor)) {
    return `<Buffer ${valor.length}B suprimido>`;
  }
  if (typeof valor === 'string') {
    return valor.length > LIMITE_LINHA
      ? valor.slice(0, LIMITE_LINHA) + `…(+${valor.length - LIMITE_LINHA} chars suprimidos)`
      : valor;
  }
  // Error passa inteiro DE PROPÓSITO: mensagem e stack são o que torna um erro
  // útil, e não carregam chave. Suprimir erro para proteger segredo seria
  // trocar um problema por outro.
  if (valor instanceof Error) return valor;

  if (valor && typeof valor === 'object') {
    if (temSegredo(valor, 0)) {
      let n = 0;
      try { n = Object.keys(valor).length; } catch (e) { /* ignora */ }
      const tipo = valor.constructor && valor.constructor.name
        ? valor.constructor.name : 'Object';
      // Só diz o que era. Serializar "com cuidado" é como isto começou.
      return `<${tipo} com material de sessão suprimido: ${n} campo(s)>`;
    }
    return valor;
  }
  return valor;
}

const _log = console.log;
const _warn = console.warn;
const _error = console.error;
function emitir(saida, args) {
  saida(`[PID ${PID_PROCESSO}]`, ...args.map(limpar));
}
console.log = (...args) => emitir(_log, args);
console.warn = (...args) => emitir(_warn, args);
console.error = (...args) => emitir(_error, args);

const PASTA_ATUAL = __dirname;
const CONFIG_PATH = path.join(PASTA_ATUAL, 'config.json');
const AUTH_PATH = path.join(PASTA_ATUAL, 'auth_alertas'); // sessão própria, separada do bot de clientes
const PASTA_ARQUIVOS_RECEBIDOS = path.join(PASTA_ATUAL, 'arquivos_recebidos'); // anexos enviados no grupo (ex: CSV do /improdutivas)
if (!fs.existsSync(PASTA_ARQUIVOS_RECEBIDOS)) {
  fs.mkdirSync(PASTA_ARQUIVOS_RECEBIDOS, { recursive: true });
}

// ---------- Configuração ----------
function carregarConfig() {
  const padrao = { grupoJid: '', porta: 3939 };
  if (!fs.existsSync(CONFIG_PATH)) {
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(padrao, null, 2), 'utf-8');
    console.log(`📝 Arquivo config.json criado em ${CONFIG_PATH}. Preencha "grupoJid" antes de usar em produção.`);
    return padrao;
  }
  try {
    const dados = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8'));
    return { ...padrao, ...dados };
  } catch (e) {
    console.log(`⚠️  Falha ao ler config.json (${e.message}). Usando valores padrão.`);
    return padrao;
  }
}

const config = carregarConfig();

let sockGlobal = null;
let conectado = false;

// Controle de backoff exponencial na reconexão -- evita o loop de
// reconectar a cada 3s indefinidamente quando algo trava (ex: motivo 405
// entrando em loop por mais de uma hora sem nunca estabilizar). Cresce
// 3s -> 6s -> 12s -> ... até um teto de 5 minutos entre tentativas, e
// zera assim que a conexão fica de pé de verdade (evento 'open').
let tentativasReconexaoSeguidas = 0;
const RECONEXAO_DELAY_BASE_MS = 3000;
const RECONEXAO_DELAY_MAX_MS = 5 * 60 * 1000;

// ---------- Fila de mensagens recebidas do grupo (para o Python fazer polling) ----------
const FILA_MENSAGENS_MAX = 300; // limite de segurança pra não crescer indefinidamente se ninguém consumir
const ESPERA_LONGA_MS = 25000; // long-polling: segura a resposta até chegar mensagem ou estourar esse tempo
let filaMensagens = []; // { participante, texto, timestamp }
let resolversPendentes = []; // callbacks de requisições /mensagens esperando mensagem nova

function avisarNovaMensagem() {
  const resolvers = resolversPendentes;
  resolversPendentes = [];
  resolvers.forEach((resolver) => resolver());
}

function extrairTextoMensagem(msg) {
  if (!msg) return '';
  if (msg.conversation) return msg.conversation;
  if (msg.extendedTextMessage && msg.extendedTextMessage.text) return msg.extendedTextMessage.text;
  return '';
}

// ---------- Versão do protocolo do WhatsApp Web ----------
// O WhatsApp troca essa versão com frequência (várias vezes por dia,
// segundo o registro do WPPConnect), e o Baileys rejeita conexões com uma
// versão desatualizada (erro 405 / "Connection Failure", sem nunca gerar
// QR Code -- ver github.com/WhiskeySockets/Baileys issues #2370, #2376,
// #2485). Fixar um número no código funciona até essa versão específica
// ser descontinuada, e aí volta a quebrar.
//
// Estratégia: buscar a versão mais recente via fetchLatestBaileysVersion()
// a cada NOVA tentativa de conexão (não só uma vez no início) -- assim,
// quando o Baileys atualizar o valor que ele próprio devolve, a próxima
// reconexão automática (já existente mais abaixo, com backoff) se
// recupera sozinha, sem precisar trocar código à mão de novo. Se a busca
// falhar (rede fora, endpoint fora do ar), cai pro valor fixo abaixo como
// última rede de segurança -- atualizado em 28/07/2026, conferido em
// https://wppconnect.io/whatsapp-versions/. Se algum dia o valor fixo
// também ficar velho e a busca automática também estiver falhando, é lá
// que se pega o número atual pra atualizar essa constante.
const VERSION_FALLBACK = [2, 3000, 1043986535];

async function obterVersaoProtocolo() {
  try {
    const { version, isLatest } = await fetchLatestBaileysVersion();
    console.log(
      `Versão de protocolo: ${version.join('.')} `
      + (isLatest ? '(confirmada como a mais recente pelo Baileys)' : '(Baileys não tem certeza se é a mais recente -- pode já estar desatualizada)')
    );
    return version;
  } catch (e) {
    console.log(`⚠️  Falha ao buscar a versão mais recente (${e.message}), usando o valor fixo ${VERSION_FALLBACK.join('.')}.`);
    return VERSION_FALLBACK;
  }
}

// ---------- Conexão com o WhatsApp ----------
async function iniciar() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_PATH);
  const version = await obterVersaoProtocolo();

  const sock = makeWASocket({
    version,
    auth: state,
    logger: pino({ level: 'silent' }),
    printQRInTerminal: false,
  });

  sockGlobal = sock;
  sock.ev.on('creds.update', saveCreds);

  // Escuta mensagens recebidas (só interessa o grupo configurado em config.json)
  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;
    for (const m of messages) {
      try {
        if (!m.message) continue; // mensagens de sistema (ex: entrou/saiu do grupo) não têm conteúdo
        if (m.key.fromMe) continue; // ignora mensagens enviadas pelo próprio bot
        if (!config.grupoJid || m.key.remoteJid !== config.grupoJid) continue; // só o grupo configurado

        const participante = m.key.participant || m.key.remoteJid;

        // Documento anexado (ex: CSV do OFS pro comando /improdutivas) --
        // baixa pro disco e enfileira com um campo "arquivo" próprio, em vez
        // de tentar encaixar isso no campo "texto" (que é só pra mensagens
        // de texto puro). O Python identifica esse tipo de mensagem
        // verificando se `arquivo` veio preenchido.
        const docMsg = m.message.documentMessage
          || (m.message.documentWithCaptionMessage && m.message.documentWithCaptionMessage.message
            && m.message.documentWithCaptionMessage.message.documentMessage);

        if (docMsg) {
          try {
            const buffer = await downloadMediaMessage(
              m,
              'buffer',
              {},
              { reuploadRequest: sock.updateMediaMessage }
            );
            const nomeOriginal = docMsg.fileName || `arquivo_${Date.now()}`;
            // Remove qualquer coisa que não seja letra/número/ponto/traço,
            // pra não ter problema salvando no disco do Windows.
            const nomeSeguro = nomeOriginal.replace(/[^a-zA-Z0-9._-]/g, '_');
            const caminhoDestino = path.join(PASTA_ARQUIVOS_RECEBIDOS, `${Date.now()}_${nomeSeguro}`);
            fs.writeFileSync(caminhoDestino, buffer);

            filaMensagens.push({
              participante,
              texto: '',
              arquivo: { nome: nomeOriginal, caminho: caminhoDestino },
              timestamp: Date.now(),
            });
            if (filaMensagens.length > FILA_MENSAGENS_MAX) {
              filaMensagens = filaMensagens.slice(-FILA_MENSAGENS_MAX);
            }
            avisarNovaMensagem();
            console.log(`📎 Arquivo recebido do grupo: "${nomeOriginal}" -> ${caminhoDestino}`);
          } catch (e) {
            console.log('⚠️  Falha ao baixar arquivo recebido do grupo:', e.message);
          }
          continue;
        }

        const texto = extrairTextoMensagem(m.message).trim();
        if (!texto) continue; // ignora mídia sem legenda, figurinhas, etc.

        filaMensagens.push({ participante, texto, timestamp: Date.now() });
        if (filaMensagens.length > FILA_MENSAGENS_MAX) {
          filaMensagens = filaMensagens.slice(-FILA_MENSAGENS_MAX);
        }
        avisarNovaMensagem();
      } catch (e) {
        console.log('⚠️  Erro ao processar mensagem recebida do grupo:', e.message);
      }
    }
  });

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      console.log('\n📱 Escaneie o QR Code abaixo com o WhatsApp que vai enviar os alertas ao grupo:\n');
      qrcode.generate(qr, { small: true });

      // Grava a string CRUA do QR num arquivo, além de desenhar no terminal.
      //
      // Por quê: quem opera esta máquina normalmente está remoto, e QR em ASCII
      // dentro de um log é ruim de escanear -- o espaçamento dos blocos depende
      // da fonte de quem está lendo. Com a string crua dá para gerar um PNG de
      // verdade em qualquer lugar.
      //
      // O arquivo é EFÊMERO de propósito: o WhatsApp rotaciona este código a
      // cada ~20s e ele vira lixo logo em seguida. Mesmo assim é credencial
      // enquanto vale (quem escanear vincula um aparelho à conta), então é
      // sobrescrito a cada QR novo e apagado assim que a conexão abre -- ver o
      // `connection === 'open'` abaixo.
      try {
        fs.writeFileSync(
          path.join(PASTA_ATUAL, 'qr_atual.txt'),
          JSON.stringify({ qr, gerado_em: new Date().toISOString() }),
          'utf8'
        );
      } catch (e) {
        console.log('⚠️  Falha ao gravar qr_atual.txt:', e.message);
      }
    }

    if (connection === 'open') {
      conectado = true;
      tentativasReconexaoSeguidas = 0;
      console.log('✅ Conectado ao WhatsApp (serviço de alertas para o grupo).');

      // Pareou: o QR nao serve mais para nada e nao tem por que continuar no
      // disco. Deixar credencial sobrando foi exatamente o problema que o log
      // deste servico teve em 08/08/2026.
      try {
        const arqQr = path.join(PASTA_ATUAL, 'qr_atual.txt');
        if (fs.existsSync(arqQr)) {
          fs.unlinkSync(arqQr);
          console.log('🧹 qr_atual.txt apagado (conexão estabelecida).');
        }
      } catch (e) {
        console.log('⚠️  Falha ao apagar qr_atual.txt:', e.message);
      }

      if (process.argv.includes('--listar-grupos')) {
        listarGrupos(sock);
      } else if (!config.grupoJid) {
        console.log('ℹ️  Nenhum "grupoJid" configurado ainda. Rode: node index.js --listar-grupos');
      }
    }

    if (connection === 'close') {
      conectado = false;
      const motivo = lastDisconnect?.error?.output?.statusCode;
      const detalheErro = lastDisconnect?.error?.message;
      const deveReconectar = motivo !== DisconnectReason.loggedOut;
      console.log(
        `⚠️  Conexão com o WhatsApp encerrada (motivo: ${motivo})`
        + (detalheErro ? ` — ${detalheErro}` : '')
        + '.'
      );
      if (deveReconectar) {
        // Backoff exponencial: 3s, 6s, 12s, 24s... até o teto de 5min.
        // Sem isso, um problema que não se resolve sozinho (ex: motivo 405
        // em loop) fica reconectando a cada 3s por horas, o que pode inclusive
        // ser interpretado pelo próprio WhatsApp como comportamento abusivo
        // e piorar o problema.
        const delay = Math.min(
          RECONEXAO_DELAY_BASE_MS * 2 ** tentativasReconexaoSeguidas,
          RECONEXAO_DELAY_MAX_MS
        );
        tentativasReconexaoSeguidas++;
        console.log(`🔄 Reconectando em ${Math.round(delay / 1000)}s (tentativa ${tentativasReconexaoSeguidas})...`);
        setTimeout(iniciar, delay);
      } else {
        tentativasReconexaoSeguidas = 0;
        console.log('❌ Sessão deslogada pelo celular. Apague a pasta "auth_alertas" e rode novamente para gerar um novo QR Code.');
      }
    }
  });
}

async function listarGrupos(sock) {
  try {
    const grupos = await sock.groupFetchAllParticipating();
    console.log('\n===== GRUPOS DISPONÍVEIS =====');
    for (const [jid, info] of Object.entries(grupos)) {
      console.log(`${info.subject}  ->  ${jid}`);
    }
    console.log('==============================');
    console.log('Copie o JID do grupo desejado para o campo "grupoJid" em config.json e reinicie o serviço.\n');
  } catch (e) {
    console.log('Erro ao listar grupos:', e.message);
  }
}

// ---------- Servidor HTTP local (recebe pedidos de alerta) ----------
const servidor = http.createServer((req, res) => {
  if (req.method === 'POST' && req.url === '/alerta') {
    let corpo = '';
    req.on('data', (chunk) => (corpo += chunk));
    req.on('end', async () => {
      res.setHeader('Content-Type', 'application/json');
      try {
        const { mensagem } = JSON.parse(corpo || '{}');

        if (!mensagem || !String(mensagem).trim()) {
          res.writeHead(400);
          return res.end(JSON.stringify({ ok: false, erro: 'campo "mensagem" ausente ou vazio' }));
        }
        if (!conectado || !sockGlobal) {
          res.writeHead(503);
          return res.end(JSON.stringify({ ok: false, erro: 'WhatsApp ainda não conectado' }));
        }
        if (!config.grupoJid) {
          res.writeHead(500);
          return res.end(JSON.stringify({ ok: false, erro: 'grupoJid não configurado em config.json (rode --listar-grupos)' }));
        }

        await sockGlobal.sendMessage(config.grupoJid, { text: mensagem });
        console.log(`📤 Alerta enviado ao grupo: "${String(mensagem).slice(0, 80)}${mensagem.length > 80 ? '...' : ''}"`);
        res.writeHead(200);
        res.end(JSON.stringify({ ok: true }));
      } catch (e) {
        res.writeHead(500);
        res.end(JSON.stringify({ ok: false, erro: e.message }));
      }
    });
  } else if (req.method === 'POST' && req.url === '/alerta-imagem') {
    // Mesmo esquema do /alerta, mas envia uma IMAGEM (ex: PNG do backlog de
    // CAPEX) em vez de texto puro. Body esperado:
    //   { "imagemBase64": "<bytes do PNG em base64, sem o prefixo data:...>",
    //     "legenda": "texto opcional que aparece junto da imagem" }
    const LIMITE_CORPO_IMAGEM_BYTES = 15 * 1024 * 1024; // 15MB em base64 é folga de sobra pra um PNG de backlog
    let corpo = '';
    let corpoGrandeDemais = false;
    req.on('data', (chunk) => {
      corpo += chunk;
      if (corpo.length > LIMITE_CORPO_IMAGEM_BYTES) {
        corpoGrandeDemais = true;
        req.destroy();
      }
    });
    req.on('end', async () => {
      if (corpoGrandeDemais) return;
      res.setHeader('Content-Type', 'application/json');
      try {
        const { imagemBase64, legenda } = JSON.parse(corpo || '{}');

        if (!imagemBase64 || !String(imagemBase64).trim()) {
          res.writeHead(400);
          return res.end(JSON.stringify({ ok: false, erro: 'campo "imagemBase64" ausente ou vazio' }));
        }
        if (!conectado || !sockGlobal) {
          res.writeHead(503);
          return res.end(JSON.stringify({ ok: false, erro: 'WhatsApp ainda não conectado' }));
        }
        if (!config.grupoJid) {
          res.writeHead(500);
          return res.end(JSON.stringify({ ok: false, erro: 'grupoJid não configurado em config.json (rode --listar-grupos)' }));
        }

        let bufferImagem;
        try {
          bufferImagem = Buffer.from(imagemBase64, 'base64');
        } catch (e) {
          res.writeHead(400);
          return res.end(JSON.stringify({ ok: false, erro: 'imagemBase64 inválido (falha ao decodificar)' }));
        }

        await sockGlobal.sendMessage(config.grupoJid, {
          image: bufferImagem,
          caption: legenda || '',
        });
        console.log(`🖼️  Imagem enviada ao grupo (${bufferImagem.length} bytes)${legenda ? ` com legenda: "${String(legenda).slice(0, 60)}..."` : ''}`);
        res.writeHead(200);
        res.end(JSON.stringify({ ok: true }));
      } catch (e) {
        res.writeHead(500);
        res.end(JSON.stringify({ ok: false, erro: e.message }));
      }
    });
  } else if (req.method === 'GET' && req.url === '/status') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ conectado, grupoJid: config.grupoJid || null }));
  } else if (req.method === 'GET' && req.url === '/mensagens') {
    res.setHeader('Content-Type', 'application/json');

    const responderComFila = () => {
      const mensagens = filaMensagens;
      filaMensagens = [];
      res.writeHead(200);
      res.end(JSON.stringify({ ok: true, mensagens }));
    };

    if (filaMensagens.length > 0) {
      responderComFila();
      return;
    }

    // Long-polling: só responde quando chegar mensagem nova no grupo ou
    // quando o tempo de espera estourar (mesmo esquema do getUpdates do
    // Telegram) -- assim o Python não precisa ficar batendo aqui sem parar.
    let respondido = false;
    const finalizar = () => {
      if (respondido) return;
      respondido = true;
      clearTimeout(temporizador);
      resolversPendentes = resolversPendentes.filter((r) => r !== finalizar);
      responderComFila();
    };

    const temporizador = setTimeout(finalizar, ESPERA_LONGA_MS);
    resolversPendentes.push(finalizar);

    req.on('close', () => {
      // Python encerrou/abandonou a conexão (ex: timeout do lado dele) --
      // só limpa, sem tentar escrever numa resposta que ninguém vai ler.
      respondido = true;
      clearTimeout(temporizador);
      resolversPendentes = resolversPendentes.filter((r) => r !== finalizar);
    });
  } else {
    res.writeHead(404);
    res.end();
  }
});

servidor.listen(config.porta, '127.0.0.1', () => {
  console.log(`🌐 Serviço de alertas do grupo escutando em http://127.0.0.1:${config.porta}`);
  console.log(`   POST /alerta     { "mensagem": "texto" }`);
  console.log(`   GET  /status`);
  console.log(`   GET  /mensagens  (fila de mensagens recebidas do grupo, consumo único)`);
});

iniciar().catch((e) => console.error('Erro fatal ao iniciar o serviço de alertas:', e));