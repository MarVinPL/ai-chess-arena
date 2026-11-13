        for pairing in pairings:
            if tournament_stop_event.is_set():
                break
            white = players.get(pairing['white_model_id']); black = players.get(pairing['black_model_id'])
            if not white or not black:
                raise RuntimeError('Brak wersji zawodnika przypisanej do pary turniejowej')
            set_live('game_prepare',f'Przygotowanie partii: {white["name"]} vs {black["name"]}', white=white['name'], black=black['name'], pairing_id=pairing['id'], moves=[], fen='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', last_move=None, response=None, proposed_move=None, legal_moves=[])
            white_assets, black_assets = _prepare_game_models(white, black)
            set_live('game_ready',f'Partia gotowa: {white["name"]} vs {black["name"]}', white=white['name'], black=black['name'], pairing_id=pairing['id'])
            game_white = {**white, **white_assets}; game_black = {**black, **black_assets}
            game_started = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            set_live('game_running','Rozpoczęto partię', white=white['name'], black=black['name'], pairing_id=pairing['id'], moves=[], fen='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', last_move=None)
            try:
                result = game_runner.play_game(
                    game_white, game_black, tournament_stop_event,
                    progress=lambda move: set_live(