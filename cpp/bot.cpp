#include <bits/stdc++.h>
#include <zmq.hpp>
#include "mensagens.pb.h"
using namespace std;

double agora() {
	return chrono::duration<double>(chrono::system_clock::now().time_since_epoch()).count();
}

const vector<string> NOMES_CANAIS = {
	"geral", "jogos", "musica", "filmes", "esportes",
	"noticias", "memes", "estudos", "tecnologia", "viagens"
};

struct Bot {
	string nome;
	zmq::context_t ctx;
	zmq::socket_t req;
	mt19937 rng;
	vector<string> canais;

	Bot(string nome_) : nome(nome_), rng(random_device{}()) {
		conecta();
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
			r.set_timestamp(agora());
			string s; r.SerializeToString(&s);
			cout << "enviado   -> " << r.ShortDebugString() << endl;
			req.send(zmq::buffer(s), zmq::send_flags::none);

			zmq::message_t m;
			if (req.recv(m, zmq::recv_flags::none)) {
				Resposta rep; rep.ParseFromArray(m.data(), m.size());
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

	void roda() {
		login();

		while (true) {
			lista();
			if (canais.size() < 5) cria_canal();
			this_thread::sleep_for(3s);
		}
	}
};

int main() {
	const char* nome = getenv("NOME");
	Bot bot(nome ? nome : "bot");
	bot.roda();

	return(0);
}
