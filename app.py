import streamlit as st
import pandas as pd
import json
import uuid
from datetime import datetime
import time
import random
import plotly.express as px

# ==============================================================================
# CONFIGURAÇÃO DA PÁGINA E ESTILO VERITAS
# ==============================================================================
st.set_page_config(page_title="Veritas FHIR | Demo MVP", page_icon="🛡️", layout="wide")

LARANJA = "#FF6B35"
AZUL_MARINHO = "#1B2838"
VERDE = "#27AE60"
VERMELHO = "#E74C3C"

st.markdown(f"""
<style>
    .main-header {{ color: {LARANJA}; font-size: 2.2rem; font-weight: bold; }}
    .metric-card {{ background-color: #f8f9fa; padding: 1rem; border-radius: 0.5rem; border-left: 5px solid {LARANJA}; }}
    .rag-box {{ background-color: #e8f4f8; padding: 1rem; border-radius: 0.5rem; border-left: 5px solid {AZUL_MARINHO}; font-family: monospace; font-size: 0.9rem; }}
</style>
""", unsafe_allow_html=True)

# ==============================================================================
# MOTOR DE REGRAS (Determinístico)
# ==============================================================================
REGRAS_MAME = {
    "89999800": {"id": "MAME-V11-047", "nome": "Material incluso na diária", "pagina": 47, "excerto": "Materiais de enfermagem e acessórios básicos já estão contemplados no valor da diária."},
    "40101028": {"id": "MAME-V11-047", "nome": "Acessório rotineiro", "pagina": 47, "excerto": "Itens acessórios já inclusos no valor da diária hospitalar."}
}

REGRAS_CMED = {
    "30101010": {"teto": 12.50, "nome": "Dipirona 500mg"},
    "30201010": {"teto": 28.90, "nome": "Omeprazol 20mg"}
}

REGRAS_CBHPM = {
    "90000101": {"id": "CBHPM-VIDEO-001", "nome": "Taxa de vídeo indevida", "pagina": 318, "excerto": "A taxa de vídeo não deve ser cobrada em duplicidade quando o procedimento principal já é por videolaparoscopia."},
    "90000201": {"id": "CBHPM-AUX-030", "nome": "Auxiliar > 30%", "pagina": 287, "excerto": "O honorário do primeiro auxiliar deve observar percentual máximo de 30% sobre o procedimento principal."}
}

def auditar_guia(guia: dict) -> dict:
    tracking_id = f"VT-{uuid.uuid4().hex[:8].upper()}"
    itens = guia.get("itens", [])
    
    valor_original = sum(i["valor_unitario"] * i["quantidade"] for i in itens)
    valor_glosado = 0.0
    regras_disparadas = []
    evidencias = []
    status_prestador = guia.get("prestador_status", "ATIVO")

    # REGRA 0: Prestador Irregular (BLOQUEIO TOTAL)
    if status_prestador == "SUSPENSO":
        return {
            "tracking_id": tracking_id,
            "decisao": "BLOQUEADO",
            "valor_original": valor_original,
            "valor_glosado": valor_original,
            "economia": valor_original,
            "regras": [{"id": "CMHPD-PRESTADOR-001", "item": "TODOS", "glosa": valor_original, "motivo": "Prestador com cadastro suspenso. Bloqueio preventivo de risco."}],
            "evidencias": [{"source": "Manual CMHPD - Cadastro", "page": 8, "excerpt": "Prestadores suspensos não devem seguir para pagamento automático.", "confidence": 0.99}]
        }

    for item in itens:
        codigo = item["codigo_tuss"]
        valor_item = item["valor_unitario"] * item["quantidade"]
        glosa_item = 0.0

        # REGRA 1: Material incluso na diária
        if codigo in REGRAS_MAME:
            glosa_item = valor_item
            regras_disparadas.append({"id": REGRAS_MAME[codigo]["id"], "item": item["descricao"], "glosa": glosa_item, "motivo": REGRAS_MAME[codigo]["nome"]})
            evidencias.append({"source": "Manual MAME V.11", "page": REGRAS_MAME[codigo]["pagina"], "excerpt": REGRAS_MAME[codigo]["excerto"], "confidence": 0.98})

        # REGRA 2: Teto CMED
        elif codigo in REGRAS_CMED and item["tipo"] == "medicamento":
            teto_total = REGRAS_CMED[codigo]["teto"] * item["quantidade"]
            if valor_item > teto_total:
                glosa_item = valor_item - teto_total
                regras_disparadas.append({"id": "CMED-TETO-001", "item": item["descricao"], "glosa": glosa_item, "motivo": f"Acima do teto CMED (R$ {REGRAS_CMED[codigo]['teto']})"})
                evidencias.append({"source": "Base CMED Oficial", "page": 12, "excerpt": "O preço máximo de venda ao governo não pode exceder o teto definido.", "confidence": 0.99})

        # REGRA 3: Videolaparoscopia Complexa
        elif codigo == "90000101": # Taxa de vídeo
            glosa_item = valor_item
            regras_disparadas.append({"id": "CBHPM-VIDEO-001", "item": item["descricao"], "glosa": glosa_item, "motivo": "Procedimento principal já é por vídeo"})
            evidencias.append({"source": "CBHPM 2024", "page": 318, "excerpt": REGRAS_CBHPM["90000101"]["excerto"], "confidence": 0.97})
        
        elif codigo == "90000201": # Auxiliar
            glosa_item = 450.00 # Simulação de excedente
            regras_disparadas.append({"id": "CBHPM-AUX-030", "item": item["descricao"], "glosa": glosa_item, "motivo": "Excede 30% do procedimento principal"})
            evidencias.append({"source": "CBHPM 2024", "page": 287, "excerpt": REGRAS_CBHPM["90000201"]["excerto"], "confidence": 0.96})

        # REGRA 4: OPME sem NF (Alerta)
        if "OPME" in item["descricao"].upper() and not item.get("nf_ou_documento"):
            regras_disparadas.append({"id": "CMHPD-OPME-NF-001", "item": item["descricao"], "glosa": 0.0, "motivo": "REVISÃO: Exige nota fiscal"})
            evidencias.append({"source": "Manual CMHPD - OPME", "page": 56, "excerpt": "Itens OPME de alto custo devem possuir nota fiscal vinculada.", "confidence": 0.97})

        valor_glosado += glosa_item

    decisao = "APROVADO" if valor_glosado == 0 else "GLOSA"
    
    return {
        "tracking_id": tracking_id,
        "decisao": decisao,
        "valor_original": valor_original,
        "valor_glosado": round(valor_glosado, 2),
        "economia": round(valor_glosado, 2),
        "regras": regras_disparadas,
        "evidencias": evidencias
    }

def gerar_guia_aleatoria():
    codigos = [
        ("40701119", "Apendicectomia", 2500.0, "cirurgico", "aberta", True),
        ("40101010", "Diária UTI", 1800.0, "diaria", None, False),
        ("89999800", "Material enfermagem", 150.0, "acessorio", None, False),
        ("30101010", "Dipirona 500mg", 25.0, "medicamento", None, False),
        ("40704012", "Colecistectomia vídeo", 3500.0, "cirurgico", "videolaparoscopica", True),
        ("90000101", "Taxa de vídeo", 1200.0, "taxa_video", "videolaparoscopica", False)
    ]
    n_itens = random.randint(2, 4)
    itens_selecionados = random.sample(codigos, n_itens)
    
    if any(i[3] == "diaria" for i in itens_selecionados) and not any(i[3] == "acessorio" for i in itens_selecionados):
        itens_selecionados.append(("89999800", "Material enfermagem", 150.0, "acessorio", None, False))

    itens = []
    for cod, desc, val, tipo, via, principal in itens_selecionados:
        item = {"codigo_tuss": cod, "descricao": desc, "quantidade": 1, "valor_unitario": val, "tipo": tipo}
        if via: item["via_acesso"] = via
        if principal: item["principal"] = True
        if tipo == "cirurgico" and via == "videolaparoscopica": item["procedimento_por_video"] = True
        itens.append(item)
        
    return {"id_guia": f"LT-{uuid.uuid4().hex[:6].upper()}", "prestador_status": "ATIVO", "itens": itens}

# ==============================================================================
# CENÁRIOS DOS 7 ATOS
# ==============================================================================
CENARIOS = {
    "Ato 1: O Caso Limpo (Aprovação Automática)": {
        "prestador_status": "ATIVO",
        "itens": [
            {"codigo_tuss": "40701119", "descricao": "Apendicectomia", "quantidade": 1, "valor_unitario": 2500.00, "tipo": "cirurgico"},
            {"codigo_tuss": "30301010", "descricao": "Ceftriaxona 1g (No teto)", "quantidade": 1, "valor_unitario": 45.20, "tipo": "medicamento"}
        ]
    },
    "Ato 2: A Diária Inflada (Glosa MAME)": {
        "prestador_status": "ATIVO",
        "itens": [
            {"codigo_tuss": "40101010", "descricao": "Diária UTI Adulto", "quantidade": 2, "valor_unitario": 1800.00, "tipo": "diaria"},
            {"codigo_tuss": "89999800", "descricao": "Kit de rouparia / material de enfermagem", "quantidade": 1, "valor_unitario": 230.00, "tipo": "acessorio"}
        ]
    },
    "Ato 3: Medicamento Acima do Teto (Glosa CMED)": {
        "prestador_status": "ATIVO",
        "itens": [
            {"codigo_tuss": "30101010", "descricao": "Dipirona 500mg (Faturado a R$ 25,00)", "quantidade": 10, "valor_unitario": 25.00, "tipo": "medicamento"},
            {"codigo_tuss": "30201010", "descricao": "Omeprazol 20mg (Faturado a R$ 45,00)", "quantidade": 1, "valor_unitario": 45.00, "tipo": "medicamento"}
        ]
    },
    "Ato 4: Videolaparoscopia Complexa (MOMENTO UAU)": {
        "prestador_status": "ATIVO",
        "itens": [
            {"codigo_tuss": "40704012", "descricao": "Colecistectomia videolaparoscópica", "quantidade": 1, "valor_unitario": 3500.00, "tipo": "cirurgico", "via_acesso": "videolaparoscopica", "principal": True, "procedimento_por_video": True},
            {"codigo_tuss": "90000101", "descricao": "Taxa de videolaparoscopia", "quantidade": 1, "valor_unitario": 1200.00, "tipo": "taxa_video", "via_acesso": "videolaparoscopica"},
            {"codigo_tuss": "90000201", "descricao": "Honorário de primeiro auxiliar", "quantidade": 1, "valor_unitario": 1500.00, "tipo": "auxiliar"},
            {"codigo_tuss": "60000503", "descricao": "Diária hospitalar", "quantidade": 2, "valor_unitario": 650.00, "tipo": "diaria"},
            {"codigo_tuss": "89999800", "descricao": "Material de enfermagem", "quantidade": 1, "valor_unitario": 180.00, "tipo": "acessorio"},
            {"codigo_tuss": "70000001", "descricao": "Pinça descartável OPME", "quantidade": 1, "valor_unitario": 2800.00, "tipo": "opme", "valor_autorizado": 2200.00, "taxa_admin_opme": 0.10, "nf_ou_documento": None}
        ]
    },
    "Ato 5: OPME sem Nota Fiscal (Alerta de Revisão)": {
        "prestador_status": "ATIVO",
        "itens": [
            {"codigo_tuss": "40704012", "descricao": "Colecistectomia videolaparoscópica", "quantidade": 1, "valor_unitario": 3500.00, "tipo": "cirurgico", "principal": True, "procedimento_por_video": True, "via_acesso": "videolaparoscopica"},
            {"codigo_tuss": "70000001", "descricao": "Pinça descartável OPME de alto custo", "quantidade": 1, "valor_unitario": 2800.00, "tipo": "opme", "valor_autorizado": 2200.00, "taxa_admin_opme": 0.10, "nf_ou_documento": None}
        ]
    },
    "Ato 6: O Prestador Irregular (BLOQUEIO TOTAL)": {
        "prestador_status": "SUSPENSO",
        "itens": [
            {"codigo_tuss": "40704012", "descricao": "Colecistectomia videolaparoscópica", "quantidade": 1, "valor_unitario": 3500.00, "tipo": "cirurgico"},
            {"codigo_tuss": "90000101", "descricao": "Taxa de videolaparoscopia", "quantidade": 1, "valor_unitario": 1200.00, "tipo": "taxa_video"}
        ]
    },
    "Ato 7: O Fechamento (Processar Lote para Dashboard)": "BATCH_MODE"
}

# ==============================================================================
# INTERFACE STREAMLIT
# ==============================================================================
st.sidebar.image("https://via.placeholder.com/200x60/FF6B35/FFFFFF?text=VERITAS+FHIR", use_container_width=True)
st.sidebar.title("Veritas FHIR")
st.sidebar.markdown("**Motor de Auditoria Clínica Inteligente**")
st.sidebar.markdown("---")
menu = st.sidebar.radio("Navegação", ["🎬 Demo ao Vivo (7 Atos)", "📊 Dashboard de ROI", "📋 Histórico"])

if "historico" not in st.session_state:
    st.session_state.historico = []

if menu == "🎬 Demo ao Vivo (7 Atos)":
    st.markdown('<p class="main-header">🎬 Demonstração ao Vivo: Os 7 Atos</p>', unsafe_allow_html=True)
    st.markdown("Selecione um cenário para ver o motor de regras e a IA citável em ação.")
    
    ato_selecionado = st.selectbox("Escolha o Cenário da Demo", list(CENARIOS.keys()))
    
    if ato_selecionado != "Ato 7: O Fechamento (Processar Lote para Dashboard)":
        col1, col2 = st.columns([3, 1])
        with col1:
            st.json(CENARIOS[ato_selecionado])
        with col2:
            st.markdown("### Ação")
            if st.button("🚀 Processar com Veritas", type="primary", use_container_width=True):
                with st.spinner("Veritas processando guia e consultando base de conhecimento RAG..."):
                    time.sleep(0.8)
                    resultado = auditar_guia(CENARIOS[ato_selecionado])
                    st.session_state.historico.append(resultado)
                    st.session_state.ultimo_resultado = resultado
                    st.rerun()

        if "ultimo_resultado" in st.session_state:
            res = st.session_state.ultimo_resultado
            st.markdown("---")
            cor_decisao = VERDE if res["decisao"] == "APROVADO" else VERMELHO if res["decisao"] == "BLOQUEADO" else LARANJA
            st.markdown(f'<h2 style="color:{cor_decisao};">Decisão: {res["decisao"]}</h2>', unsafe_allow_html=True)
            
            c1, c2, c3 = st.columns(3)
            c1.metric("Valor Original", f"R$ {res['valor_original']:,.2f}")
            c2.metric("Valor Glosado/Bloqueado", f"R$ {res['valor_glosado']:,.2f}", delta=f"-{res['economia']:,.2f}", delta_color="inverse" if res['valor_glosado'] > 0 else "off")
            c3.metric("Tracking ID", res["tracking_id"])

            if res["regras"]:
                st.subheader("📜 Regras Disparadas & Evidência RAG (Explainable AI)")
                for i, regra in enumerate(res["regras"]):
                    with st.expander(f"Regra: {regra['id']} | Item: {regra['item']} | Glosa: R$ {regra['glosa']:,.2f}", expanded=True):
                        st.write(f"**Motivo:** {regra['motivo']}")
                        if i < len(res["evidencias"]):
                            ev = res["evidencias"][i]
                            st.markdown(f"""
                            <div class="rag-box">
                            <strong>🔍 Evidência Documental Citável:</strong><br>
                            📚 <strong>Fonte:</strong> {ev['source']}<br>
                            📄 <strong>Página:</strong> {ev['page']}<br>
                            💬 <strong>Trecho:</strong> "<em>{ev['excerpt']}</em>"<br>
                            🎯 <strong>Confiança do Modelo:</strong> {ev['confidence']*100:.0f}%
                            </div>
                            """, unsafe_allow_html=True)
    else:
        # LÓGICA DO ATO 7
        st.markdown("### 🎯 O Grande Fechamento: Impacto Acumulado")
        st.markdown("Este ato simula o processamento de um lote real de guias para demonstrar como o **Dashboard de ROI** se alimenta em tempo real.")
        if st.button("🚀 ATO 7: Processar Lote de 10 Guias Aleatórias", type="primary", use_container_width=True):
            with st.spinner("Processando lote em paralelo e alimentando o Data Lake..."):
                time.sleep(1.5)
                for _ in range(10):
                    guia = gerar_guia_aleatoria()
                    resultado = auditar_guia(guia)
                    st.session_state.historico.append(resultado)
                
                st.success("✅ Lote de 10 guias processado com sucesso!")
                st.info("👉 **Agora, clique na aba '📊 Dashboard de ROI'** no menu lateral esquerdo para ver o impacto financeiro acumulado em tempo real. É assim que o CFO enxerga o valor do Veritas.")

elif menu == "📊 Dashboard de ROI":
    st.markdown('<p class="main-header">📊 Dashboard Executivo de ROI</p>', unsafe_allow_html=True)
    
    total_guias = len(st.session_state.historico)
    total_economia = sum(r["economia"] for r in st.session_state.historico)
    total_glosas = sum(1 for r in st.session_state.historico if r["decisao"] in ["GLOSA", "BLOQUEADO"])
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Guias Processadas (Demo)", total_guias)
    c2.metric("💰 Economia Gerada (Demo)", f"R$ {total_economia:,.2f}")
    c3.metric("Glosas/Bloqueios", total_glosas)
    c4.metric("Projeção Anual (10k guias/mês)", "R$ 3,8 Milhões", delta="Baseado em 4% de glosa")

    if total_guias > 0:
        col1, col2 = st.columns(2)
        with col1:
            decisoes = [r["decisao"] for r in st.session_state.historico]
            fig_pie = px.pie(names=decisoes, title="Distribuição de Decisões", color=decisoes, color_discrete_map={"APROVADO": VERDE, "GLOSA": LARANJA, "BLOQUEADO": VERMELHO})
            st.plotly_chart(fig_pie, use_container_width=True)
        
        with col2:
            df = pd.DataFrame(st.session_state.historico)
            df["Guia"] = df["tracking_id"]
            fig_bar = px.bar(df, x="Guia", y="economia", title="Economia por Guia Processada", color="economia", color_continuous_scale="Oranges")
            st.plotly_chart(fig_bar, use_container_width=True)

        st.markdown("---")
        st.subheader("📋 Histórico Detalhado da Sessão")
        st.dataframe(df[["tracking_id", "decisao", "valor_original", "valor_glosado", "economia"]], use_container_width=True)
    else:
        st.info("💡 Vá até a aba '🎬 Demo ao Vivo' e execute os Atos 1 a 7 para ver o dashboard se encher de dados e calcular o ROI em tempo real.")

elif menu == "📋 Histórico":
    st.markdown('<p class="main-header">📋 Histórico Completo de Auditorias</p>', unsafe_allow_html=True)
    if st.session_state.historico:
        df = pd.DataFrame(st.session_state.historico)
        st.dataframe(df, use_container_width=True)
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Exportar Auditorias para CSV", csv, "veritas_auditorias.csv", "text/csv")
    else:
        st.warning("Nenhuma auditoria realizada ainda nesta sessão.")

st.sidebar.markdown("---")
st.sidebar.markdown("**Veritas FHIR v1.0-MVP**")
st.sidebar.markdown("🔒 100% On-Premise Ready | LGPD Compliant")
st.sidebar.markdown("Desenvolvido por Alexandre Alves")
