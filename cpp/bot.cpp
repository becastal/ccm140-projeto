#include <bits/stdc++.h>
#include <zmq.hpp>
#include <openssl/sha.h>
#include "mensagens.pb.h"
using namespace std;

double agora() {
	return chrono::duration<double>(chrono::system_clock::now().time_since_epoch()).count();
}

// 1794969144.4334 -> "14:32:24.433"
string hora(double t) {
	time_t s = (time_t)t;
	char buf[32]; strftime(buf, sizeof buf, "%H:%M:%S", localtime(&s));
	char ms[8]; snprintf(ms, sizeof ms, ".%03d", (int)((t - s) * 1000));
	return string(buf) + ms;
}

const vector<string> NOMES_CANAIS = {
	"geral", "jogos", "musica", "filmes", "esportes", "noticias", "memes", "estudos", "tecnologia", "viagens"
};

const vector<string> FRASES = {
	"oi gente", "alguem ai?", "bom dia", "boa noite", "que dia hein", "alguem viu o jogo ontem?", "to com fome", "kkkkkkk", "concordo", "discordo totalmente", "isso ai", "vou dormir", "voltei", "falou"
};

// ---------- prova de conhecimento zero (isomorfismo de grafos) ----------

const int N = 20, RODADAS = 128;
typedef vector<vector<int>> Grafo;

// aresta (i, j) com i < j vira o bit k, na ordem (0,1), (0,2), ..., (18,19)
string codifica(const Grafo& G) {
	string s((N * (N - 1) / 2 + 7) / 8, 0);
	for (int i = 0, k = 0; i < N; i++) {
		for (int j = i + 1; j < N; j++, k++) {
			if (G[i][j]) s[k / 8] |= 1 << (k % 8);
		}
	}
	return s;
}

Grafo decodifica(const string& s) {
	Grafo G(N, vector<int>(N));
	for (int i = 0, k = 0; i < N; i++) {
		for (int j = i + 1; j < N; j++, k++) {
			G[i][j] = G[j][i] = (s[k / 8] >> (k % 8)) & 1;
		}
	}
	return G;
}

// troca os rotulos: a aresta (i, j) vira (p[i], p[j])
Grafo permuta(const vector<int>& p, const Grafo& G) {
	Grafo H(N, vector<int>(N));
	for (int i = 0; i < N; i++) {
		for (int j = 0; j < N; j++) {
			if (G[i][j]) H[p[i]][p[j]] = 1;
		}
	}
	return H;
}

string hexa(const string& s) {
	string r;
	char b[3];
	for (unsigned char c : s) {
		snprintf(b, sizeof b, "%02x", c);
		r += b;
	}
	return r;
}

string de_hexa(const string& s) {
	string r;
	for (int i = 0; i + 1 < (int)s.size(); i += 2) {
		r += (char)stoi(s.substr(i, 2), nullptr, 16);
	}
	return r;
}

// as moedas da prova saem do hash (Fiat-Shamir), assim o login continua sendo 1 REQ e 1 REP
string moedas(const string& usuario, const Prova& p) {
	string s = usuario;
	for (int b = 7; b >= 0; b--) s += (char)((p.instante() >> (8 * b)) & 0xff);
	s += p.g0() + p.g1();
	for (const string& h : p.h()) s += h;

	unsigned char d[SHA256_DIGEST_LENGTH];
	SHA256((const unsigned char*)s.data(), s.size(), d);
	return string((char*)d, SHA256_DIGEST_LENGTH);
}

int moeda(const string& m, int i) {
	return ((unsigned char)m[i / 8] >> (i % 8)) & 1;
}

struct Chave {
	Grafo G0, G1;
	vector<int> pi; // o segredo: G1 = pi(G0)

	// le a chave do disco; se nao existir, cria uma nova e salva
	void carrega(const string& nome, mt19937& rng) {
		string sec = "/app/chaves/privadas/" + nome + ".sec";
		string pub = "/app/chaves/publicas/" + nome + ".pub";

		ifstream f(sec);
		if (f) {
			pi.assign(N, 0);
			for (int& x : pi) f >> x;
			string g0; f >> g0;
			G0 = decodifica(de_hexa(g0));
			G1 = permuta(pi, G0);
			cout << "chave lida de " << sec << endl;
			return;
		}

		G0.assign(N, vector<int>(N));
		for (int i = 0; i < N; i++) {
			for (int j = i + 1; j < N; j++) {
				G0[i][j] = G0[j][i] = rng() % 2;
			}
		}
		pi.resize(N);
		iota(pi.begin(), pi.end(), 0);
		shuffle(pi.begin(), pi.end(), rng);
		G1 = permuta(pi, G0);

		ofstream fs(sec);
		for (int x : pi) fs << x << " ";
		fs << "\n" << hexa(codifica(G0)) << "\n";
		ofstream fp(pub);
		fp << hexa(codifica(G0)) << "\n" << hexa(codifica(G1)) << "\n";
		cout << "chave nova criada em " << sec << " e " << pub << endl;
	}

	// so a parte publica (o que o impostor consegue ler)
	bool carrega_publica(const string& nome) {
		ifstream f("/app/chaves/publicas/" + nome + ".pub");
		string g0, g1;
		if (!(f >> g0 >> g1)) return false;
		G0 = decodifica(de_hexa(g0));
		G1 = decodifica(de_hexa(g1));
		return true;
	}
};

// ------------------------------------------------------------------------

string mostra(Requisicao r) {
	// os 32 compromissos e respostas deixariam a linha enorme
	if (r.has_prova() and r.prova().h_size() > 0) {
		int k = r.prova().h_size();
		r.mutable_prova()->clear_h();
		r.mutable_prova()->clear_resposta();
		return r.ShortDebugString() + " (+" + to_string(k) + " compromissos e respostas)";
	}
	return r.ShortDebugString();
}

struct Bot {
	string nome;
	zmq::context_t ctx;
	zmq::socket_t req, sub;
	mt19937 rng;
	vector<string> canais;
	set<string> inscritos;
	long long relogio = 0; // relogio logico
	string sessao;
	Chave chave;

	Bot(string nome_) : nome(nome_), sub(ctx, zmq::socket_type::sub), rng(random_device{}()) {
		conecta();
		sub.connect("tcp://proxy:5558");
	}

	void conecta() {
		req = zmq::socket_t(ctx, zmq::socket_type::req);
		req.set(zmq::sockopt::rcvtimeo, 3000);
		req.set(zmq::sockopt::linger, 0);
		req.connect("tcp://broker:5555");
	}

	// manda um pedido e espera a resposta (se demorar, reconecta e tenta de novo)
	Resposta pede(Requisicao r) {
		r.set_usuario(nome);
		if (r.tipo() != "login") r.set_sessao(sessao);

		while (true) {
			r.set_relogio(++relogio);
			r.set_timestamp(agora());
			string s; r.SerializeToString(&s);
			cout << "enviado   -> " << mostra(r) << endl;
			req.send(zmq::buffer(s), zmq::send_flags::none);

			zmq::message_t m;
			if (req.recv(m, zmq::recv_flags::none)) {
				Resposta rep; rep.ParseFromArray(m.data(), m.size());
				relogio = max(relogio, (long long)rep.relogio());
				cout << "recebido  <- " << rep.ShortDebugString() << endl;
				return rep;
			}

			cout << "sem resposta do servidor, tentando de novo" << endl;
			conecta();
		}
	}

	// prova que conhece pi sem mostrar pi
	Prova gera_prova() {
		Prova p;
		p.set_n(N);
		p.set_g0(codifica(chave.G0));
		p.set_g1(codifica(chave.G1));
		p.set_instante((long long)(agora() * 1000));

		// compromissos: H = sigma(G0), com um sigma novo em cada rodada
		vector<vector<int>> S(RODADAS, vector<int>(N));
		for (auto& s : S) {
			iota(s.begin(), s.end(), 0);
			shuffle(s.begin(), s.end(), rng);
			p.add_h(codifica(permuta(s, chave.G0)));
		}

		string m = moedas(nome, p);
		vector<int> inv(N);
		for (int x = 0; x < N; x++) inv[chave.pi[x]] = x;

		// moeda 0: mostra sigma (leva G0 em H); moeda 1: mostra sigma o pi^-1 (leva G1 em H)
		for (int i = 0; i < RODADAS; i++) {
			vector<int> r = S[i];
			if (moeda(m, i)) {
				for (int x = 0; x < N; x++) r[x] = S[i][inv[x]];
			}
			p.add_resposta(string(r.begin(), r.end()));
		}
		return p;
	}

	void login() {
		while (true) {
			Requisicao r; r.set_tipo("login");
			*r.mutable_prova() = gera_prova();
			Resposta rep = pede(r);
			if (rep.status() == "ok") {
				sessao = rep.sessao();
				return;
			}
			this_thread::sleep_for(1s);
		}
	}

	// se a sessao nao for aceita (ex.: a replica do login ainda nao chegou), faz login de novo
	Resposta pede_logado(const Requisicao& r) {
		Resposta rep = pede(r);
		if (rep.status() == "erro" and rep.erro() == "sessao invalida") {
			login();
			rep = pede(r);
		}
		return rep;
	}

	void lista() {
		Requisicao r; r.set_tipo("listar");
		Resposta rep = pede(r);
		canais.assign(rep.canais().begin(), rep.canais().end());
	}

	void cria_canal() {
		vector<string> livres;
		for (const string& c : NOMES_CANAIS) {
			if (find(canais.begin(), canais.end(), c) == canais.end()) livres.push_back(c);
		}
		if (livres.empty()) return;

		Requisicao r; r.set_tipo("canal");
		r.set_canal(livres[rng() % livres.size()]);
		Resposta rep = pede_logado(r);
		if (rep.status() == "ok") canais.push_back(r.canal());
	}

	void inscreve() {
		vector<string> livres;
		for (const string& c : canais) {
			if (!inscritos.count(c)) livres.push_back(c);
		}
		if (livres.empty()) return;

		string c = livres[rng() % livres.size()];
		sub.set(zmq::sockopt::subscribe, c);
		inscritos.insert(c);
		cout << "inscrito em [" << c << "] (" << inscritos.size() << " canais)" << endl;
	}

	// espera ate o instante t, mostrando o que chegar dos canais
	void espera(double t) {
		while (true) {
			double falta = t - agora();
			if (falta <= 0) return;

			zmq::pollitem_t item = {sub.handle(), 0, ZMQ_POLLIN, 0};
			zmq::poll(&item, 1, chrono::milliseconds((int)(falta * 1000) + 1));
			if (!(item.revents & ZMQ_POLLIN)) continue;

			zmq::message_t topico, m;
			(void)sub.recv(topico);
			(void)sub.recv(m);
			double chegou = agora();

			Publicacao p; p.ParseFromArray(m.data(), m.size());
			if (!inscritos.count(p.canal())) continue; // "geral" tambem pega "geral2" no filtro do zmq
			relogio = max(relogio, (long long)p.relogio());

			cout << "mensagem  <= [" << p.canal() << "] " << p.usuario() << ": \"" << p.texto() << "\""
				<< " (enviada " << hora(p.timestamp()) << ", recebida " << hora(chegou) << ") | "
				<< p.ShortDebugString() << endl;
		}
	}

	void publica(const string& canal) {
		Requisicao r; r.set_tipo("publicar");
		r.set_canal(canal);
		r.set_texto(FRASES[rng() % FRASES.size()]);
		pede_logado(r);
	}

	void roda() {
		chave.carrega(nome, rng);
		login();

		while (true) {
			lista();
			if (canais.size() < 5) cria_canal();
			if (inscritos.size() < 3) inscreve();
			if (canais.empty()) {
				espera(agora() + 1);
				continue;
			}

			string canal = canais[rng() % canais.size()];
			for (int i = 0; i < 10; i++) {
				publica(canal);
				espera(agora() + 1);
			}
		}
	}

	// ---------- impostor: tenta se passar pelo bot "nome" sem saber o segredo dele ----------

	void ataca() {
		this_thread::sleep_for(15s); // deixa o bot verdadeiro fazer o primeiro login

		for (int tentativa = 0; ; tentativa = (tentativa + 1) % 3) {
			Requisicao r;

			if (tentativa == 0) {
				cout << "IMPOSTOR: tentando entrar como " << nome << " com uma chave inventada" << endl;
				Chave verdadeira = chave;
				chave.G0.assign(N, vector<int>(N));
				for (int i = 0; i < N; i++) {
					for (int j = i + 1; j < N; j++) chave.G0[i][j] = chave.G0[j][i] = rng() % 2;
				}
				chave.pi.resize(N);
				iota(chave.pi.begin(), chave.pi.end(), 0);
				shuffle(chave.pi.begin(), chave.pi.end(), rng);
				chave.G1 = permuta(chave.pi, chave.G0);
				r.set_tipo("login");
				*r.mutable_prova() = gera_prova();
				chave = verdadeira;
			}

			else if (tentativa == 1) {
				if (!chave.carrega_publica(nome)) {
					cout << "IMPOSTOR: ainda nao achei a chave publica de " << nome << endl;
					this_thread::sleep_for(5s);
					continue;
				}
				cout << "IMPOSTOR: tentando entrar como " << nome << " com a chave publica dele, chutando as moedas" << endl;
				// sem pi so da para responder o lado que foi chutado em cada rodada
				Prova p;
				p.set_n(N);
				p.set_g0(codifica(chave.G0));
				p.set_g1(codifica(chave.G1));
				p.set_instante((long long)(agora() * 1000));
				for (int i = 0; i < RODADAS; i++) {
					vector<int> s(N);
					iota(s.begin(), s.end(), 0);
					shuffle(s.begin(), s.end(), rng);
					p.add_h(codifica(permuta(s, rng() % 2 ? chave.G1 : chave.G0)));
					p.add_resposta(string(s.begin(), s.end()));
				}
				r.set_tipo("login");
				*r.mutable_prova() = p;
			}

			else {
				cout << "IMPOSTOR: tentando publicar como " << nome << " com uma sessao inventada" << endl;
				sessao = "0123456789abcdef";
				r.set_tipo("publicar");
				r.set_canal("geral");
				r.set_texto("eu sou o " + nome + " de verdade");
			}

			Resposta rep = pede(r);
			cout << "IMPOSTOR: " << (rep.status() == "ok" ? "CONSEGUIU ENTRAR (nao devia!)" : "barrado: " + rep.erro()) << endl;
			this_thread::sleep_for(5s);
		}
	}
};

int main() {
	const char* nome = getenv("NOME");
	const char* alvo = getenv("ALVO");

	if (alvo) {
		Bot impostor(alvo);
		impostor.ataca();
	} else {
		Bot bot(nome ? nome : "bot");
		bot.roda();
	}

	return(0);
}
