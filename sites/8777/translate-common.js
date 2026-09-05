(() => {
  "use strict";
  document.documentElement.lang = "en";

  const phrases = [
    ["A Copa do Mundo como você nunca viu, em tempo real", "The World Cup as you've never seen it, live"],
    ["Tabela de pontos atualizada minuto a minuto, condições de jogo ao vivo e reprodução dos lances mais incríveis. Tudo em um só lugar.", "Minute-by-minute standings, live match conditions, and replays of the most incredible plays. All in one place."],
    ["Acompanhe a tabela de pontos em tempo real, o placar ao vivo de cada partida e reveja os melhores momentos — tudo em um só lugar.", "Follow real-time standings and every live score, then relive the best moments — all in one place."],
    ["O painel chega povoado e nunca para: cada número se refaz caractere por caractere, e só a linha que mudou se move. Filtre, ordene, inclua ou remova — o fluxo continua.", "The board arrives populated and never stops: every number flips character by character, and only the changed row moves. Filter, sort, add, or remove — the flow continues."],
    ["A direção aparece duas vezes por linha: na forma, antes dos dígitos, e no selo colorido depois deles.", "Direction appears twice on every row: as a shape before the digits and as a colored badge after them."],
    ["placar, minuto e dados com a mesma mecânica de dígitos — se o dado atrasa, o valor anterior permanece", "score, match time, and data use the same digit mechanism — if data is delayed, the previous value remains"],
    ["Clique nos cabeçalhos para ordenar. As posições reagem aos gols marcados ao vivo.", "Click the headers to sort. Positions respond to goals scored live."],
    ["Cadastre-se gratuitamente para acompanhar seus times, receber alertas de gol em tempo real e concorrer a uma camisa oficial autografada.", "Sign up free to follow your teams, receive real-time goal alerts, and enter to win a signed official jersey."],
    ["Placar, posse de bola, finalizações e o minuto a minuto de cada batalha em campo.", "Score, possession, shots, and a minute-by-minute view of every battle on the pitch."],
    ["Reveja os melhores momentos, gols e defesas espetaculares da competição.", "Relive the tournament's best moments, goals, and spectacular saves."],
    ["Placares e cronômetros atualizados automaticamente durante os jogos.", "Scores and clocks update automatically during matches."],
    ["Gols, defesas e lances decisivos — reveja em segundos.", "Goals, saves, and decisive plays — relive them in seconds."],
    ["Acompanhe os principais lances desta partida.", "Follow the key moments from this match."],
    ["Central ao vivo da Copa do Mundo 2026: tabela de pontos em tempo real, condições de jogo ao vivo, lances em vídeo e replay quadro a quadro.", "World Cup 2026 live center: real-time standings, live match conditions, video highlights, and frame-by-frame replay."],
    ["A ABERTURA SE FECHA NOS DOIS EXTREMOS DO TRECHO", "THE OPENING CLOSES AT BOTH ENDS OF THE SEQUENCE"],
    ["GOL AOS 78' — FINALIZACAO DE PRIMEIRA", "GOAL AT 78' — FIRST-TIME FINISH"],
    ["CRUZAMENTO RASTEIRO NO SEGUNDO PAU", "LOW CROSS TO THE FAR POST"],
    ["INVERSAO LONGA EM DOIS TOQUES", "LONG SWITCH IN TWO TOUCHES"],
    ["TABELINHA DE PRIMEIRA NO CORREDOR", "ONE-TOUCH COMBINATION DOWN THE CHANNEL"],
    ["ROUBADA DE BOLA NA INTERMEDIARIA", "BALL WON IN MIDFIELD"],
    ["SEM AVANCO AUTOMATICO", "NO AUTOPLAY"],
    ["ROLE PARA REPRODUZIR", "SCROLL TO PLAY"],
    ["A emoção da Copa,", "The thrill of the World Cup,"],
    ["minuto a minuto", "minute by minute"],
    ["TRANSMISSÃO AO VIVO AGORA", "LIVE BROADCAST NOW"],
    ["Tabela de Pontos em Tempo Real", "Real-Time Standings"],
    ["Pontuação atualizada automaticamente conforme os jogos acontecem.", "Points update automatically as matches unfold."],
    ["Condições de Jogo em Tempo Real", "Real-Time Match Conditions"],
    ["Reprodução dos Lances Maravilhosos", "Spectacular Play Replays"],
    ["Palpite e Concorra a Prêmios", "Make Your Pick and Win Prizes"],
    ["Classificado para as oitavas de final", "Qualified for the round of 16"],
    ["Pular para a tabela de classificação", "Skip to the standings table"],
    ["Contagem regressiva para a próxima grande partida", "Countdown to the next big match"],
    ["Contagem regressiva para o apito inicial", "Countdown to kickoff"],
    ["Temporada 2026 · Fase de Grupos", "2026 Season · Group Stage"],
    ["Situação em tempo real", "Real-time status"],
    ["Classificação em tempo real", "Real-time standings"],
    ["Atualizado em tempo real. Ordenação padrão por pontos.", "Updated in real time. Default sorting by points."],
    ["Próxima grande partida", "Next big match"],
    ["Final · Brasil × França", "Final · Brazil × France"],
    ["Classificado (1º–2º)", "Qualified (1st–2nd)"],
    ["Repescagem (3º)", "Playoff (3rd)"],
    ["V vitória · E empate · D derrota", "W win · D draw · L loss"],
    ["Tabela de pontos em tempo real", "Real-time standings"],
    ["Tabela de pontos", "Standings"],
    ["Partidas ao vivo", "Live matches"],
    ["Melhores Momentos", "Highlights"], ["Melhores momentos", "Highlights"],
    ["Reprodução maravilhosa", "Spectacular replay"],
    ["Promoção Oficial", "Official Giveaway"], ["Participar Grátis", "Join for Free"],
    ["Entrar na Promoção", "Enter the Giveaway"], ["Quero Participar", "I Want to Join"],
    ["Ver Jogos Ao Vivo", "Watch Live Matches"], ["Assistir agora", "Watch now"],
    ["Ver classificação", "View standings"], ["Ver todos os melhores momentos", "View all highlights"],
    ["Ver tudo", "View all"], ["Detalhes da partida", "Match details"],
    ["AO VIVO / CONDIÇÕES DE GUERRA", "LIVE / MATCH CONDITIONS"],
    ["Placar & Estatísticas", "Score & Statistics"], ["TABELA DE PONTOS", "STANDINGS"],
    ["MELHORES MOMENTOS", "HIGHLIGHTS"], ["FINAL · Grupo de Elite", "FINAL · Elite Group"],
    ["Grupo A · Estádio Central", "Group A · Central Stadium"],
    ["Pular para o conteúdo", "Skip to content"], ["Navegação principal", "Main navigation"],
    ["Abrir menu", "Open menu"], ["Fechar reprodução", "Close replay"],
    ["Reproduzindo lance...", "Playing highlight..."], ["Atualizando…", "Updating…"],
    ["3 jogos ao vivo", "3 live matches"],
    ["48 seleções · 16 sedes · 104 partidas", "48 teams · 16 host cities · 104 matches"],
    ["↓ próximo lance · ↑ lance anterior", "↓ next highlight · ↑ previous highlight"],
    ["sem som · clique no véu ou em ✕ para fechar", "muted · click the overlay or ✕ to close"],
    ["03 — painel mecânico", "03 — mechanical board"],
    ["fluxo contínuo · sem parada", "continuous flow · no pauses"],
    ["Filtrar seleções", "Filter teams"], ["Filtrar sigla ou seleção", "Filter code or team"],
    ["Adicionar sigla", "Add team code"], ["linhas em exibição", "rows displayed"],
    ["Direção da variação", "Change direction"], ["Painel vazio.", "Empty board."],
    ["Adicione uma sigla", "Add a team code"], ["no campo acima para começar.", "in the field above to begin."],
    ["Nenhuma linha corresponde a", "No rows match"], ["Maiores variações", "Largest changes"],
    ["mesmo conjunto", "same dataset"], ["Aguardando a primeira variação do painel.", "Waiting for the board's first change."],
    ["Como ler o painel", "How to read the board"], ["triângulo cheio para cima", "solid upward triangle"],
    ["a seleção subiu", "the team moved up"], ["triângulo cheio para baixo", "solid downward triangle"],
    ["a seleção caiu", "the team moved down"], ["traço neutro — linha em repouso", "neutral dash — unchanged row"],
    ["Condições de jogo agora", "Match conditions now"], ["Condições de jogo", "Match conditions"],
    ["painel mecânico", "mechanical board"], ["Remover", "Remove"], ["do painel", "from the board"],
    ["em alta", "rising"], ["em queda", "falling"], ["estável", "stable"],
    ["sinal instável · último dado", "unstable signal · last data"], ["atualizado", "updated"],
    ["tendência", "trend"], ["Seleção", "Team"], ["Seleções", "Teams"],
    ["Partidas", "Matches"], ["Gols", "Goals"], ["Jogos", "Played"],
    ["Vitórias", "Wins"], ["Empates", "Draws"], ["Derrotas", "Losses"],
    ["Últimos 5", "Last 5"], ["Forma", "Form"], ["Classificação", "Standings"],
    ["Tabela", "Standings"], ["Ao Vivo", "Live"], ["AO VIVO", "LIVE"], ["ao vivo", "live"],
    ["Lances", "Highlights"], ["Promoção", "Giveaway"], ["Contagem", "Countdown"],
    ["Vídeo", "Video"], ["Dias", "Days"], ["Horas", "Hours"], ["Seg", "Sec"],
    ["Posse de bola", "Possession"], ["Posse", "Possession"], ["Finalizações", "Shots"],
    ["Grupo", "Group"], ["Encerrado", "Finished"], ["Aguardando início da partida.", "Waiting for kickoff."],
    ["Hoje", "Today"], ["Detalhes", "Details"], ["Reprise", "Replay"],
    ["Programada", "Scheduled"], ["Resultado final", "Final score"], ["Ao vivo agora", "Live now"],
    ["vitória", "win"], ["empate", "draw"], ["derrota", "loss"],
    ["Golaço", "Wonder goal"], ["Defesa", "Save"], ["Gol", "Goal"], ["Pênalti", "Penalty"],
    ["duração", "duration"], ["contra", "versus"],
    ["Brasil", "Brazil"], ["França", "France"], ["Croácia", "Croatia"], ["Suíça", "Switzerland"],
    ["Camarões", "Cameroon"], ["Holanda", "Netherlands"], ["Países Baixos", "Netherlands"],
    ["Alemanha", "Germany"], ["Espanha", "Spain"], ["Inglaterra", "England"],
    ["Uruguai", "Uruguay"], ["Gana", "Ghana"], ["Japão", "Japan"], ["Marrocos", "Morocco"],
    ["México", "Mexico"], ["Canadá", "Canada"], ["Colômbia", "Colombia"], ["Bélgica", "Belgium"],
    ["Itália", "Italy"], ["Dinamarca", "Denmark"], ["Coreia do Sul", "South Korea"],
    ["Austrália", "Australia"], ["Equador", "Ecuador"], ["Sérvia", "Serbia"], ["Noruega", "Norway"],
    ["Egito", "Egypt"], ["Nigéria", "Nigeria"], ["Paraguai", "Paraguay"], ["Estados Unidos", "United States"],
    ["Copa do Mundo", "World Cup"], ["Copa Mundial", "World Cup"],
    ["Central Ao Vivo", "Live Center"], ["Central ao Vivo", "Live Center"], ["Central ao vivo", "Live Center"],
    ["como você nunca viu, em tempo real", "as you've never seen it, live"],
    ["Estádio Maracanã", "Maracanã Stadium"], ["Estádio Nacional", "National Stadium"],
    ["Estádio Beira-Rio", "Beira-Rio Stadium"], ["Estádio Azteca", "Azteca Stadium"],
    ["Estádio", "Stadium"], ["PLACAR", "SCORE"], ["No alvo", "On target"],
    ["Gol de", "Goal by"], ["Cartão amarelo", "Yellow card"], ["Fim de jogo", "Full time"],
    ["Golaço de bicicleta — Brasil x Argentina", "Bicycle-kick wonder goal — Brazil vs Argentina"],
    ["Defesa milagrosa aos 89'", "Miraculous save in the 89th minute"],
    ["Drible desconcertante no meio", "Dazzling dribble through midfield"],
    ["Cobrança de falta indefensável", "Unstoppable free kick"],
    ["Contra-ataque relâmpago", "Lightning counterattack"],
    ["Pintura de fora da área", "Stunning strike from outside the box"],
    ["Vinícius Júnior emenda de primeira num voleio perfeito no ângulo.", "Vinícius Júnior meets it first time with a perfect volley into the top corner."],
    ["O goleiro voa no canto e garante os três pontos no último lance.", "The goalkeeper flies into the corner and secures all three points on the final play."],
    ["Sequência de fintas que deixa dois marcadores no chão.", "A sequence of feints leaves two defenders on the ground."],
    ["A bola faz uma curva impossível por cima da barreira.", "The ball curls impossibly over the wall."],
    ["Da defesa ao ataque em oito segundos e finalização precisa.", "From defense to attack in eight seconds, finished with precision."],
    ["Chute colocado de trivela que morre na gaveta.", "An outside-of-the-foot strike nestles in the top corner."],
    ["Dados demonstrativos", "Demonstration data"], ["Eliminado", "Eliminated"],
    ["Voleio de Neymar de fora da área", "Neymar volley from outside the box"],
    ["Milagre do goleiro no último minuto", "Goalkeeper miracle in the final minute"],
    ["Cobrança de falta perfeita", "Perfect free kick"],
    ["Passe de calcanhar genial", "Brilliant backheel pass"],
    ["Contra-ataque em 8 segundos", "Counterattack in 8 seconds"],
    ["Cavadinha decisiva na disputa", "Decisive chipped penalty in the shootout"],
    ["Central de acompanhamento em tempo real", "Real-time match center"],
    ["Copa", "World Cup"],
    ["LANCE EM DESTAQUE · BRA 1 X 0 SUI · CAMERA 12", "FEATURED PLAY · BRA 1–0 SUI · CAMERA 12"],
    ["ROLAGEM = TEMPO", "SCROLL = TIME"], ["AVANCANDO", "FORWARD"],
    ["RETROCEDENDO", "REWINDING"], ["CONGELADO", "PAUSED"],
    ["QUADRO", "FRAME"], ["QPS", "FPS"], ["GOL", "GOAL"], ["DEFESA", "SAVE"],
    ["central ao vivo", "live center"], ["lances", "highlights"], ["tabela", "standings"],
    ["quadro a quadro", "frame by frame"], ["LANCES", "HIGHLIGHTS"], ["TABELA", "STANDINGS"],
    ["QUADRO A QUADRO", "FRAME BY FRAME"], ["Câmera aérea", "Aerial camera"],
    ["PRÉ-JOGO", "PRE-MATCH"], ["Cerimônia de abertura", "Opening ceremony"],
    ["Entrada das seleções", "Teams enter the pitch"], ["sem som", "muted"], ["SEM SOM", "MUTED"],
    ["Voleio no ângulo", "Volley into the top corner"], ["Defesa impossível", "Impossible save"],
    ["Falta por cima da barreira", "Free kick over the wall"], ["Bola parada", "Set piece"],
    ["A arquibancada canta", "The stands erupt in song"], ["INTERVALO", "HALF-TIME"],
    ["Torcida", "Supporters"], ["Arena em festa", "Stadium celebration"], ["PÓS-JOGO", "POST-MATCH"],
    ["Show de luzes", "Light show"], ["Aérea", "Aerial"], ["01 — abertura", "01 — opening"],
    ["role para ver os lances", "scroll to view highlights"], ["02 — lances", "02 — highlights"],
    ["Reprodução maravilhosa", "Spectacular replay"], ["a rolagem move a fileira", "scrolling moves the reel"],
    ["reproduzindo", "playing"], ["Reprodução:", "Replay:"], ["Abrir", "Open"],
    ["HOJE", "TODAY"], ["AMANHÃ", "TOMORROW"], ["SEDE", "HOST CITY"], ["RECORDE", "RECORD"],
    ["Cidade do México", "Mexico City"], ["Nova Jersey", "New Jersey"],
    ["2.240 m de altitude", "2,240 m elevation"], ["104.213 torcedores no Rose Bowl", "104,213 fans at the Rose Bowl"],
    ["gramado híbrido", "hybrid pitch"], ["gol mais rápido", "fastest goal"],
    ["teto retrátil", "retractable roof"], ["umidade", "humidity"], ["vento", "wind"],
    ["41 gols na primeira semana", "41 goals in the first week"], ["em tempo real", "in real time"],
    ["incluir", "add"], ["INCLUIR", "ADD"], ["Pos", "Pos"], ["Sigla", "Code"], ["SIGLA", "CODE"],
    ["linhas", "rows"], ["em exibição", "displayed"], ["Var", "Change"],
    ["agora", "now"], ["AGORA", "NOW"], ["2ª fase", "Round of 16"], ["MINUTO", "MINUTE"],
    ["POSSE", "POSSESSION"], ["CHUTES", "SHOTS"], ["reproduzindo ·", "playing ·"],
    ["05 — fechamento", "05 — closing"], ["A bola volta ao centro", "The ball returns to the center spot"],
    ["O painel continua rodando enquanto você estiver aqui. Nada termina, nada zera — a próxima atualização já está a caminho.", "The board keeps running while you're here. Nothing ends or resets — the next update is already on its way."],
    ["Onde assistir hoje", "Where to watch today"], ["Escalação do dia no seu e-mail", "Today's lineup in your inbox"],
    ["Um aviso por rodada, com os lances que valem revisão quadro a quadro.", "One update per round, featuring the plays worth reviewing frame by frame."],
    ["Seu e-mail", "Your email"], ["quero receber", "subscribe"],
    ["Pronto. Chega antes do apito inicial.", "Done. It will arrive before kickoff."],
    ["abertura", "opening"], ["fechamento", "closing"],
    ["Peça promocional fictícia · dados simulados · imagens geradas por IA", "Fictional promotional piece · simulated data · AI-generated images"],
    ["3 hat-tricks em 48 h", "3 hat-tricks in 48 h"],
    ["minuto", "minute"], ["posse", "possession"], ["chutes", "shots"],
    ["A bola volta", "The ball returns"], ["ao centro", "to the center spot"],
    ["Por favor, informe seu nome.", "Please enter your name."], ["Informe um e-mail válido.", "Enter a valid email address."],
    ["Você está participando da promoção.", "You're entered in the giveaway."],
    ["Principais lances de", "Key moments from"],
    ["Gol! Neymar abre o placar de cabeça.", "Goal! Neymar opens the scoring with a header."],
    ["Cartão amarelo para De Paul.", "Yellow card for De Paul."],
    ["Empate da Argentina em jogada de escanteio.", "Argentina equalize from a corner."],
    ["Substituição: entra Vini Jr., sai Rodrygo.", "Substitution: Vini Jr. replaces Rodrygo."],
    ["Gol! Vini Jr. recoloca o Brasil na frente.", "Goal! Vini Jr. puts Brazil back in front."],
    ["Reproduzir:", "Play:"], ["Em breve", "Coming soon"]
  ].sort((a, b) => b[0].length - a[0].length);

  const phraseMap = new Map(phrases);
  const pattern = new RegExp(phrases.map(([source]) => source.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "g");
  const translate = (value) => value.replace(pattern, (source) => phraseMap.get(source) || source);
  const selfUpdated = new WeakSet();

  const translateNode = (root) => {
    if (root.nodeType === Node.TEXT_NODE) {
      const next = translate(root.nodeValue || "");
      if (next !== root.nodeValue) {
        selfUpdated.add(root);
        root.nodeValue = next;
      }
      return;
    }
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
    const element = root.nodeType === Node.ELEMENT_NODE ? root : null;
    if (element) for (const attribute of ["aria-label", "title", "placeholder", "data-match"]) {
      if (element.hasAttribute(attribute)) {
        const value = element.getAttribute(attribute) || "";
        const next = translate(value);
        if (next !== value) element.setAttribute(attribute, next);
      }
    }
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (node.parentElement?.matches("script, style, noscript")) continue;
      const next = translate(node.nodeValue || "");
      if (next !== node.nodeValue) {
        selfUpdated.add(node);
        node.nodeValue = next;
      }
    }
  };

  translateNode(document);
  new MutationObserver((records) => {
    for (const record of records) {
      if (record.type === "characterData") {
        if (selfUpdated.has(record.target)) selfUpdated.delete(record.target);
        else translateNode(record.target);
      }
      for (const node of record.addedNodes) translateNode(node);
    }
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true });
})();
