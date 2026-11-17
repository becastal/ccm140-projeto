#include <bits/stdc++.h>
#include <zmq.hpp>
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
	"geral", "jogos", "musica", "filmes", "esportes",
	"noticias", "memes", "estudos", "tecnologia", "viagens"
};

const vector<string> FRASES = {
	"oi gente", "alguem ai?", "bom dia", "boa noite", "que dia hein",
	"alguem viu o jogo ontem?", "to com fome", "kkkkkkk", "concordo",
	"discordo totalmente", "isso ai", "vou dormir", "voltei", "falou"
};

struct Bot {
	string nome;
	zmq::context_t ctx;
	zmq::socket_t req, sub;
	mt19937 rng;
	vector<string> canais;
	set<string> inscritos;
	long long relogio = 0; // relogio logico

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

		while (true) {
			r.set_relogio(++relogio);
			r.set_timestamp(agora());
			string s; r.SerializeToString(&s);
			cout << "enviado   -> " << r.ShortDebugString() << endl;
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

	void login() {
		while (true) {
			Requisicao r; r.set_tipo("login");
			Resposta rep = pede(r);
			if (rep.status() == "ok") return;
			this_thread::sleep_for(1s);
		}
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
		Resposta rep = pede(r);
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
		pede(r);
	}

	void roda() {
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
};

int main() {
	const char* nome = getenv("NOME");
	Bot bot(nome ? nome : "bot");
	bot.roda();

	return(0);
}
