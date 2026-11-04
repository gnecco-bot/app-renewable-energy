from django.shortcuts import render
import requests
from datetime import datetime
import os
from google import genai
from django.core.cache import cache  # para o cache

def pagina_inicial(request):
    contexto = {}
    if request.method == "POST":
        cep = request.POST.get("cep")
        dados_endereco = consultar_cep(cep)

        if dados_endereco:
            contexto['endereco'] = dados_endereco
            
            cidade = dados_endereco.get('localidade')
            uf = dados_endereco.get('uf')
            coords = obter_coordenadas(cidade, uf)
            
            if coords:
                previsao = consultar_previsao_tempo(coords['latitude'], coords['longitude'])
                if previsao:
                    contexto['clima'] = previsao

                    # Chamada da IA (agora com cache baseado no CEP)
                    conclusao_ia = analisar_potencial_solar_ia(previsao, dados_endereco, cep)
                    contexto['conclusao_ia'] = conclusao_ia

                else:
                    contexto['erro_clima'] = "Não foi possível carregar os dados de previsão do tempo."
            else:
                contexto['erro_clima'] = "Não foi possível localizar as coordenadas da cidade."
        else:
            contexto['erro'] = "CEP não encontrado ou inválido."
            
    return render(request, 'html/pagina_inicial.html', contexto)


def analisar_potencial_solar_ia(dados_clima, dados_endereco, cep):
    """Envia os dados para o Gemini com cache para evitar esperas repetidas."""
    
    # Chave única de cache para a IA baseada no CEP (válido por 1 hora)
    cache_key = f'ia_solar_{cep}'
    resposta_cache = cache.get(cache_key)
    if resposta_cache is not None:
        print("-> [Cache Hit] Resposta da IA recuperada do cache!")
        return resposta_cache

    api_key = os.environ.get("GEMINI_API_KEY", "PUT YOUR KEY GOOGLE AI")
    if not api_key:
        return "Chave de API do Gemini não configurada."

    cidade = dados_endereco.get('localidade', '')
    uf = dados_endereco.get('uf', '')
    semana = dados_clima.get('semana', [])

    prompt = f"""
    (regras: em vez de utilizar "**" em destaques em negrito, coloque a palavra entre "<strong></strong>")
    Esta análise superficial não precisa se aprofundar muito, previsão básica para resposta concisa e rápida.
    Analise o potencial solar de {cidade}/{uf}:
    Atual: Temp {dados_clima.get('temp_atual')}°C, Umidade {dados_clima.get('umidade')}%, Irradiância {dados_clima.get('irradiancia_direta')} W/m².
    Previsão para os próximos 7 dias: {semana}

    Responda em poucas linhas e de forma objetiva em Markdown:
    - Potencial: (Excelente, Bom, Moderado ou Baixo)
    - Picos: Dia de maior e menor geração da semana
    - Possivel desconto: Possível desconto na conta de luz que poderá ter com base na geração de energia
    - Total: Total possível gerado nesta semana
    """

    client = genai.Client(api_key=api_key)
    modelos = ['gemini-2.5-flash', 'gemini-2.5-pro', 'gemini-3.5-flash', 'gemini-3.6-flash']
    
    ultimo_erro = None
    texto_resposta = None

    for modelo in modelos:
        try:
            response = client.models.generate_content(
                model=modelo,
                contents=prompt
            )
            texto_resposta = response.text
            print(f'funcionei no "{modelo}"')
            break
        except Exception as e:
            ultimo_erro = e
            continue

    if texto_resposta:
        # Salva no cache por 3600 segundos (1 hora)
        cache.set(cache_key, texto_resposta, 3600)
        return texto_resposta
    
    return f"Servidores da IA temporariamente indisponíveis devido à alta demanda. (Erro: {str(ultimo_erro)})"


def consultar_cep(cep):
    cep_clean = ''.join(filter(str.isdigit, str(cep)))
    if len(cep_clean) != 8:
        return None

    # Cache do CEP por 24 horas (endereço não muda)
    cache_key = f'cep_{cep_clean}'
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    url = f'https://viacep.com.br/ws/{cep_clean}/json/'
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            dados = response.json()
            if 'erro' not in dados:
                cache.set(cache_key, dados, 86400) # 24 horas
                return dados
    except requests.RequestException:
        return None

    return None


def obter_coordenadas(cidade, uf):
    """Converte cidade e estado em Latitude e Longitude com cache."""
    cidade_clean = cidade.strip().lower()
    cache_key = f'coords_{cidade_clean}_{uf}'
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    url = f'https://geocoding-api.open-meteo.com/v1/search?name={cidade}&count=5&language=pt&format=json'
    
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            results = response.json().get('results')
            if results:
                coords = {
                    'latitude': results[0]['latitude'],
                    'longitude': results[0]['longitude']
                }
                cache.set(cache_key, coords, 86400) # Cache por 24h
                return coords
    except requests.RequestException:
        return None
    return None


def consultar_previsao_tempo(latitude, longitude):
    """Busca a previsão do tempo com cache curto (ex: 30 minutos)."""
    lat_r = round(float(latitude), 2)
    lon_r = round(float(longitude), 2)
    cache_key = f'previsao_{lat_r}_{lon_r}'
    
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    url = (
        f'https://api.open-meteo.com/v1/forecast?'
        f'latitude={latitude}&longitude={longitude}'
        f'&current=temperature_2m,relative_humidity_2m,wind_speed_10m,direct_normal_irradiance'
        f'&daily=temperature_2m_max,temperature_2m_min,shortwave_radiation_sum,wind_speed_10m_max'
        f'&forecast_days=7'
        f'&timezone=America%2FSao_Paulo'
    )
    
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            dados = response.json()
            current = dados.get('current', {})
            daily = dados.get('daily', {})

            previsao_semana = []
            datas = daily.get('time', [])
            maximas = daily.get('temperature_2m_max', [])
            minimas = daily.get('temperature_2m_min', [])
            radiacoes = daily.get('shortwave_radiation_sum', [])
            ventos_max = daily.get('wind_speed_10m_max', [])

            for i in range(len(datas)):
                data_formatada = datetime.strptime(datas[i], "%Y-%m-%d").strftime("%d/%m")
                previsao_semana.append({
                    'data': data_formatada,
                    'temp_max': maximas[i],
                    'temp_min': minimas[i],
                    'radiacao_total': radiacoes[i],
                    'vento_max': ventos_max[i]
                })

            resultado = {
                'temp_atual': current.get('temperature_2m'),
                'umidade': current.get('relative_humidity_2m'),
                'vento_velocidade': current.get('wind_speed_10m'),
                'irradiancia_direta': current.get('direct_normal_irradiance'),
                'semana': previsao_semana
            }
            
            # Cache do clima por 30 minutos (1800 segundos) pois o tempo muda
            cache.set(cache_key, resultado, 1800)
            return resultado
    except requests.RequestException:
        return None
    return None