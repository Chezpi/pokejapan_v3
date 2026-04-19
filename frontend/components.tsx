// PokeJapan — Frontend completo
// pages/index.tsx  (página principal)
// pages/card/[id].tsx  (página de detalle)
// Todo en un solo archivo para simplicidad — separar en producción

// ════════════════════════════════════════
// CONSTANTES Y TIPOS
// ════════════════════════════════════════

export const COMM       = 0.05;   // 5% tu comisión
export const FRIEND_JPY = 1000;   // comisión fija amigo Japón (¥)
export const EUR_JPY    = 162;
export const FX: Record<string, number> = {
  EUR: 1, USD: 1.08, GBP: 0.85, JPY: EUR_JPY,
  CAD: 1.47, AUD: 1.65, CHF: 0.96,
};
export const SYM: Record<string, string> = {
  EUR:'€', USD:'$', GBP:'£', JPY:'¥', CAD:'CA$', AUD:'A$', CHF:'CHF ',
};

// IVA de importación por país destino
export const IMPORT_TAX: Record<string, number> = {
  ES: 0.21,   // España 21%
  DE: 0.19,   // Alemania 19%
  FR: 0.20,   // Francia 20%
  IT: 0.22,   // Italia 22%
  PT: 0.23,   // Portugal 23%
  NL: 0.21,   // Países Bajos 21%
  BE: 0.21,   // Bélgica 21%
  AT: 0.20,   // Austria 20%
  PL: 0.23,   // Polonia 23%
  GB: 0.20,   // Reino Unido 20%
  CH: 0.077,  // Suiza 7.7%
  US: 0.00,   // EEUU: depende del estado, estimamos 0 federal
  CA: 0.05,   // Canadá 5% federal
  AU: 0.10,   // Australia 10%
  MX: 0.16,   // México 16%
  OTHER: 0.20 // Resto del mundo ~20% estimado
};

export const COUNTRY_NAMES: Record<string, string> = {
  ES:'España', DE:'Alemania', FR:'Francia', IT:'Italia',
  PT:'Portugal', NL:'Países Bajos', BE:'Bélgica', AT:'Austria',
  PL:'Polonia', GB:'Reino Unido', CH:'Suiza', US:'Estados Unidos',
  CA:'Canadá', AU:'Australia', MX:'México', OTHER:'Otro país'
};

// Estados japoneses A/B/C/D
export const GRADES = [
  { grade: 'A',  label: 'Estado A',  desc: 'Perfecta. Sin marcas visibles. Como nueva.',          multiplier: 1.00 },
  { grade: 'B',  label: 'Estado B',  desc: 'Casi perfecta. Marcas mínimas solo bajo luz directa.', multiplier: 0.80 },
  { grade: 'C',  label: 'Estado C',  desc: 'Uso leve. Pequeños roces visibles en los bordes.',     multiplier: 0.60 },
  { grade: 'D',  label: 'Estado D',  desc: 'Uso notable. Arañazos o marcas claramente visibles.',  multiplier: 0.42 },
];

export type Grade = 'A' | 'B' | 'C' | 'D';

export interface CardListing {
  id: number;
  tcgdex_id: string;
  name_en: string;
  name_ja: string;
  set_name: string;
  set_id: string;
  rarity: string;
  category: 'carta' | 'caja' | 'promo';
  image_url: string;
  // Precio base (estado A) de cada fuente en yenes
  sources: {
    source: 'pokecazilla' | 'sneakerdunk';
    price_jpy: number;   // estado A
    url: string;
    images: string[];    // fotos del vendedor
  }[];
}

export interface CartItem {
  card: CardListing;
  grade: Grade;
  source: string;
  price_jpy: number;     // precio en yenes para ese estado
  price_final: number;   // precio final en moneda seleccionada
}

// ════════════════════════════════════════
// CÁLCULO DE PRECIO FINAL
// ════════════════════════════════════════

export function calcPrice(
  base_jpy: number,
  grade: Grade,
  country: string,
  currency: string
): {
  base_jpy:    number;
  grade_jpy:   number;
  friend_jpy:  number;
  comm_jpy:    number;
  subtotal_jpy:number;
  tax_rate:    number;
  tax_jpy:     number;
  total_jpy:   number;
  total_final: number;
  display:     string;
} {
  const gradeMultiplier = GRADES.find(g => g.grade === grade)?.multiplier ?? 1;
  const grade_jpy    = Math.round(base_jpy * gradeMultiplier);
  const friend_jpy   = FRIEND_JPY;
  const comm_jpy     = Math.round((grade_jpy + friend_jpy) * COMM);
  const subtotal_jpy = grade_jpy + friend_jpy + comm_jpy;
  const tax_rate     = IMPORT_TAX[country] ?? IMPORT_TAX.OTHER;
  const tax_jpy      = Math.round(subtotal_jpy * tax_rate);
  const total_jpy    = subtotal_jpy + tax_jpy;

  const eur_amount   = total_jpy / EUR_JPY;
  const total_final  = parseFloat((eur_amount * FX[currency]).toFixed(2));

  const sym = SYM[currency];
  const display = currency === 'JPY'
    ? `¥${total_jpy.toLocaleString('es-ES')}`
    : `${sym}${total_final.toFixed(2)}`;

  return {
    base_jpy, grade_jpy, friend_jpy, comm_jpy,
    subtotal_jpy, tax_rate, tax_jpy, total_jpy,
    total_final, display,
  };
}

// ════════════════════════════════════════
// COMPONENTE: BANNER DE AVISO ADUANAS
// ════════════════════════════════════════

export function CustomsBanner({ country, onChangeCountry }: {
  country: string;
  onChangeCountry: (c: string) => void;
}) {
  const rate = IMPORT_TAX[country] ?? IMPORT_TAX.OTHER;
  const pct  = Math.round(rate * 100);

  return (
    <div style={{
      background: '#FFF8E6',
      borderBottom: '1px solid #F5C475',
      padding: '10px 20px',
      display: 'flex',
      alignItems: 'center',
      gap: '10px',
      flexWrap: 'wrap',
      fontSize: '12px',
    }}>
      <span style={{ fontSize: '15px' }}>⚠️</span>
      <span style={{ color: '#633806', fontWeight: 500 }}>
        Envío y aduanas: el precio final incluye el IVA de importación de tu país ({pct}%) y
        todos los costes de gestión. El envío internacional se calculará al finalizar el pedido
        según el peso total.
      </span>
      <select
        value={country}
        onChange={e => onChangeCountry(e.target.value)}
        style={{
          marginLeft: 'auto',
          background: '#fff',
          border: '1px solid #F5C475',
          borderRadius: '6px',
          padding: '4px 8px',
          fontSize: '12px',
          fontFamily: 'inherit',
          color: '#633806',
          cursor: 'pointer',
        }}
      >
        {Object.entries(COUNTRY_NAMES).map(([code, name]) => (
          <option key={code} value={code}>{name} ({Math.round((IMPORT_TAX[code] ?? 0.20) * 100)}%)</option>
        ))}
      </select>
    </div>
  );
}

// ════════════════════════════════════════
// COMPONENTE: SELECTOR DE ESTADO (A/B/C/D)
// ════════════════════════════════════════

export function GradeSelector({
  selected,
  onSelect,
  basePriceJpy,
  country,
  currency,
}: {
  selected: Grade;
  onSelect: (g: Grade) => void;
  basePriceJpy: number;
  country: string;
  currency: string;
}) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: '6px' }}>
      {GRADES.map(g => {
        const { display } = calcPrice(basePriceJpy, g.grade as Grade, country, currency);
        const isOn = selected === g.grade;
        return (
          <div
            key={g.grade}
            onClick={() => onSelect(g.grade as Grade)}
            style={{
              border: isOn ? '2px solid #e8001a' : '1px solid #e0e0e0',
              borderRadius: '10px',
              padding: '10px 8px',
              cursor: 'pointer',
              background: isOn ? '#fff5f5' : '#fff',
              textAlign: 'center',
              transition: 'all .12s',
            }}
          >
            <div style={{
              fontSize: '16px', fontWeight: 700,
              color: isOn ? '#e8001a' : '#333',
              marginBottom: '2px',
            }}>
              {g.grade}
            </div>
            <div style={{ fontSize: '11px', color: '#888', marginBottom: '4px' }}>
              {g.grade === 'A' ? 'Perfecta' : g.grade === 'B' ? 'Casi nueva' : g.grade === 'C' ? 'Leve uso' : 'Usada'}
            </div>
            <div style={{ fontSize: '12px', fontWeight: 600, color: '#111' }}>
              {display}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ════════════════════════════════════════
// COMPONENTE: DESGLOSE DE PRECIO
// ════════════════════════════════════════

export function PriceBreakdown({
  base_jpy,
  grade,
  country,
  currency,
}: {
  base_jpy: number;
  grade: Grade;
  country: string;
  currency: string;
}) {
  const p = calcPrice(base_jpy, grade, country, currency);
  const sym = currency === 'JPY' ? '¥' : SYM[currency];
  const fmt = (jpy: number) => {
    if (currency === 'JPY') return `¥${jpy.toLocaleString('es-ES')}`;
    const eur = jpy / EUR_JPY;
    return `${sym}${(eur * FX[currency]).toFixed(2)}`;
  };

  const rows = [
    { label: 'Precio carta (estado ' + grade + ')', value: fmt(p.grade_jpy) },
    { label: 'Gestión local en Japón',              value: fmt(p.friend_jpy), note: '¥1.000 fijo' },
    { label: 'Comisión de servicio (5%)',            value: fmt(p.comm_jpy) },
    { label: `IVA importación ${COUNTRY_NAMES[country]} (${Math.round(p.tax_rate * 100)}%)`, value: fmt(p.tax_jpy) },
  ];

  return (
    <div style={{
      background: '#f9f9f9',
      border: '1px solid #e8e8e8',
      borderRadius: '10px',
      overflow: 'hidden',
      fontSize: '13px',
    }}>
      {rows.map((row, i) => (
        <div key={i} style={{
          display: 'flex',
          justifyContent: 'space-between',
          padding: '8px 14px',
          borderBottom: '1px solid #f0f0f0',
          alignItems: 'center',
        }}>
          <span style={{ color: '#666' }}>
            {row.label}
            {row.note && <span style={{ fontSize: '10px', color: '#bbb', marginLeft: '5px' }}>{row.note}</span>}
          </span>
          <span style={{ fontWeight: 500, color: '#111' }}>{row.value}</span>
        </div>
      ))}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        padding: '10px 14px',
        fontWeight: 700,
        fontSize: '15px',
        background: '#fff',
      }}>
        <span>Total (sin envío)</span>
        <span style={{ color: '#e8001a' }}>{p.display}</span>
      </div>
      <div style={{
        padding: '8px 14px',
        fontSize: '11px',
        color: '#aaa',
        background: '#fff',
        borderTop: '1px solid #f0f0f0',
      }}>
        + Envío internacional calculado al confirmar el pedido según peso y destino
      </div>
    </div>
  );
}

// ════════════════════════════════════════
// COMPONENTE: TARJETA EN EL GRID
// ════════════════════════════════════════

export function ProductCard({
  card,
  country,
  currency,
  onOpen,
  onAddCart,
}: {
  card: CardListing;
  country: string;
  currency: string;
  onOpen: (card: CardListing) => void;
  onAddCart: (card: CardListing, grade: Grade, source: string) => void;
}) {
  const cheapest = [...card.sources].sort((a, b) => a.price_jpy - b.price_jpy)[0];
  if (!cheapest) return null;

  const priceA = calcPrice(cheapest.price_jpy, 'A', country, currency);
  const priceD = calcPrice(cheapest.price_jpy, 'D', country, currency);

  const RARITY_STYLE: Record<string, React.CSSProperties> = {
    SAR: { background: '#FAEEDA', color: '#633806' },
    MUR: { background: '#FAEEDA', color: '#633806' },
    SR:  { background: '#E6F1FB', color: '#0C447C' },
    HR:  { background: '#FBEAF0', color: '#72243E' },
    AR:  { background: '#EAF3DE', color: '#27500A' },
    RR:  { background: '#F1EFE8', color: '#444441' },
    PR:  { background: '#FBEAF0', color: '#72243E' },
    BOX: { background: '#FAEEDA', color: '#633806' },
  };
  const rarStyle = RARITY_STYLE[card.rarity] ?? RARITY_STYLE.RR;

  return (
    <div
      style={{
        background: '#fff',
        border: '1px solid #e8e8e8',
        borderRadius: '12px',
        overflow: 'hidden',
        cursor: 'pointer',
        transition: 'transform .12s, border-color .12s',
      }}
      onMouseEnter={e => {
        (e.currentTarget as HTMLDivElement).style.transform = 'translateY(-2px)';
        (e.currentTarget as HTMLDivElement).style.borderColor = '#bbb';
      }}
      onMouseLeave={e => {
        (e.currentTarget as HTMLDivElement).style.transform = '';
        (e.currentTarget as HTMLDivElement).style.borderColor = '#e8e8e8';
      }}
      onClick={() => onOpen(card)}
    >
      {/* Imagen */}
      <div style={{
        height: '130px',
        background: '#f5f5f7',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        position: 'relative',
        overflow: 'hidden',
      }}>
        {card.image_url
          ? <img src={card.image_url} alt={card.name_en}
              style={{ width: '100%', height: '100%', objectFit: 'contain', padding: '8px' }} />
          : <div style={{ fontSize: '48px', opacity: .1, fontWeight: 700 }}>
              {card.category === 'caja' ? '□' : card.category === 'promo' ? '★' : '◆'}
            </div>
        }
        <div style={{
          position: 'absolute', top: '7px', left: '7px',
          fontSize: '9px', fontWeight: 700, padding: '2px 6px',
          borderRadius: '4px', letterSpacing: '.5px', ...rarStyle,
        }}>
          {card.rarity}
        </div>
        <div style={{
          position: 'absolute', bottom: '7px', right: '7px',
          fontSize: '10px', background: 'rgba(0,0,0,.55)', color: '#fff',
          padding: '2px 7px', borderRadius: '20px', fontWeight: 500,
        }}>
          Ver fotos →
        </div>
      </div>

      {/* Info */}
      <div style={{ padding: '10px 12px' }}>
        <div style={{
          fontSize: '12px', fontWeight: 600, color: '#111',
          marginBottom: '1px', whiteSpace: 'nowrap',
          overflow: 'hidden', textOverflow: 'ellipsis',
        }}>
          {card.name_en}
        </div>
        <div style={{ fontSize: '10px', color: '#aaa', marginBottom: '8px' }}>
          {card.set_name}
        </div>

        {/* Rango de precio */}
        <div style={{ marginBottom: '8px' }}>
          <div style={{ fontSize: '18px', fontWeight: 700, color: '#111' }}>
            {priceA.display}
          </div>
          <div style={{ fontSize: '11px', color: '#aaa' }}>
            Estado A · desde {priceD.display} en estado D
          </div>
        </div>

        {/* Grades rápidos */}
        <div style={{ display: 'flex', gap: '4px', marginBottom: '8px' }}>
          {(['A','B','C','D'] as Grade[]).map(g => {
            const { display } = calcPrice(cheapest.price_jpy, g, country, currency);
            return (
              <div key={g} style={{
                flex: 1, textAlign: 'center', background: '#f5f5f7',
                borderRadius: '6px', padding: '4px 2px',
              }}>
                <div style={{ fontSize: '10px', fontWeight: 700, color: '#333' }}>{g}</div>
                <div style={{ fontSize: '9px', color: '#888' }}>{display}</div>
              </div>
            );
          })}
        </div>

        <button
          onClick={e => { e.stopPropagation(); onAddCart(card, 'A', cheapest.source); }}
          style={{
            width: '100%', background: '#e8001a', color: '#fff',
            border: 'none', borderRadius: '8px', padding: '7px 0',
            fontSize: '12px', fontWeight: 600, cursor: 'pointer',
            fontFamily: 'inherit',
          }}
        >
          Añadir al carrito (estado A)
        </button>
      </div>
    </div>
  );
}

// ════════════════════════════════════════
// COMPONENTE: MODAL DETALLE DE CARTA
// ════════════════════════════════════════

export function CardDetail({
  card,
  country,
  currency,
  onClose,
  onAddCart,
}: {
  card: CardListing;
  country: string;
  currency: string;
  onClose: () => void;
  onAddCart: (card: CardListing, grade: Grade, source: string) => void;
}) {
  const [grade,     setGrade    ] = React.useState<Grade>('A');
  const [source,    setSource   ] = React.useState(card.sources[0]?.source ?? '');
  const [photoIdx,  setPhotoIdx ] = React.useState(0);

  const selectedSource = card.sources.find(s => s.source === source) ?? card.sources[0];
  const photos = selectedSource?.images ?? [];
  const base_jpy = selectedSource?.price_jpy ?? 0;
  const { display } = calcPrice(base_jpy, grade, country, currency);

  const SOURCE_LABEL: Record<string, string> = {
    pokecazilla: 'Pokécazilla',
    sneakerdunk: 'SneakerDunk',
  };

  return (
    <div
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,.55)',
        display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
        padding: '20px', zIndex: 1000, overflowY: 'auto',
      }}
      onClick={onClose}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: '#fff', borderRadius: '16px',
          width: '100%', maxWidth: '680px',
          overflow: 'hidden', marginTop: '20px',
        }}
      >
        {/* Header */}
        <div style={{
          background: '#e8001a', padding: '14px 18px',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <div>
            <div style={{ fontSize: '16px', fontWeight: 700, color: '#fff' }}>{card.name_en}</div>
            <div style={{ fontSize: '12px', color: 'rgba(255,255,255,.75)' }}>{card.set_name} · {card.rarity}</div>
          </div>
          <button onClick={onClose} style={{
            background: 'rgba(255,255,255,.2)', border: 'none', color: '#fff',
            width: '30px', height: '30px', borderRadius: '50%',
            fontSize: '16px', cursor: 'pointer', fontFamily: 'inherit',
          }}>✕</button>
        </div>

        <div style={{ padding: '18px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '18px' }}>

          {/* Columna izquierda: fotos */}
          <div>
            {/* Foto principal */}
            <div style={{
              height: '220px', background: '#f5f5f7', borderRadius: '10px',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              marginBottom: '8px', overflow: 'hidden', position: 'relative',
            }}>
              {photos.length > 0
                ? <img src={photos[photoIdx]} alt="" style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
                : card.image_url
                  ? <img src={card.image_url} alt={card.name_en} style={{ width: '100%', height: '100%', objectFit: 'contain', padding: '12px' }} />
                  : <div style={{ fontSize: '80px', opacity: .08 }}>◆</div>
              }
              {photos.length > 1 && (
                <div style={{
                  position: 'absolute', bottom: '8px', right: '8px',
                  fontSize: '10px', background: 'rgba(0,0,0,.5)', color: '#fff',
                  padding: '2px 7px', borderRadius: '10px',
                }}>
                  {photoIdx + 1}/{photos.length}
                </div>
              )}
            </div>

            {/* Thumbnails */}
            {photos.length > 1 && (
              <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap' }}>
                {photos.map((ph, i) => (
                  <img
                    key={i} src={ph} alt=""
                    onClick={() => setPhotoIdx(i)}
                    style={{
                      width: '48px', height: '48px', objectFit: 'cover',
                      borderRadius: '6px', cursor: 'pointer',
                      border: i === photoIdx ? '2px solid #e8001a' : '1px solid #e0e0e0',
                    }}
                  />
                ))}
              </div>
            )}

            {/* Fuente */}
            <div style={{ marginTop: '12px' }}>
              <div style={{ fontSize: '11px', fontWeight: 600, color: '#888', letterSpacing: '.5px', marginBottom: '6px' }}>
                DISPONIBLE EN
              </div>
              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                {card.sources.map(s => (
                  <button
                    key={s.source}
                    onClick={() => setSource(s.source)}
                    style={{
                      padding: '6px 12px', borderRadius: '8px', fontSize: '12px',
                      fontWeight: 500, cursor: 'pointer', fontFamily: 'inherit',
                      border: source === s.source ? '2px solid #e8001a' : '1px solid #e0e0e0',
                      background: source === s.source ? '#fff5f5' : '#f9f9f9',
                      color: source === s.source ? '#e8001a' : '#666',
                    }}
                  >
                    {SOURCE_LABEL[s.source] ?? s.source}
                    <span style={{ fontSize: '11px', marginLeft: '5px', color: '#aaa' }}>
                      ¥{s.price_jpy.toLocaleString()}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Columna derecha: estado + precio */}
          <div>
            <div style={{ fontSize: '11px', fontWeight: 600, color: '#888', letterSpacing: '.5px', marginBottom: '8px' }}>
              ESTADO DE LA CARTA
            </div>

            {/* Descripción del estado seleccionado */}
            <div style={{
              background: '#f9f9f9', border: '1px solid #e8e8e8',
              borderRadius: '8px', padding: '10px 12px', marginBottom: '10px',
              fontSize: '12px', color: '#555', lineHeight: 1.5,
            }}>
              <span style={{ fontWeight: 700, color: '#e8001a', marginRight: '6px' }}>
                Estado {grade}
              </span>
              {GRADES.find(g => g.grade === grade)?.desc}
            </div>

            <GradeSelector
              selected={grade}
              onSelect={setGrade}
              basePriceJpy={base_jpy}
              country={country}
              currency={currency}
            />

            <div style={{ margin: '14px 0' }}>
              <div style={{ fontSize: '11px', fontWeight: 600, color: '#888', letterSpacing: '.5px', marginBottom: '8px' }}>
                DESGLOSE DEL PRECIO
              </div>
              <PriceBreakdown
                base_jpy={base_jpy}
                grade={grade}
                country={country}
                currency={currency}
              />
            </div>

            <button
              onClick={() => { onAddCart(card, grade, source); onClose(); }}
              style={{
                width: '100%', background: '#e8001a', color: '#fff',
                border: 'none', borderRadius: '10px', padding: '12px',
                fontSize: '14px', fontWeight: 700, cursor: 'pointer',
                fontFamily: 'inherit',
              }}
            >
              Añadir al carrito — {display}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ════════════════════════════════════════
// COMPONENTE: RESUMEN EN CHECKOUT
// ════════════════════════════════════════

export function CheckoutSummary({
  items,
  country,
  currency,
}: {
  items: CartItem[];
  country: string;
  currency: string;
}) {
  const fmt = (jpy: number) => {
    if (currency === 'JPY') return `¥${jpy.toLocaleString('es-ES')}`;
    const eur = jpy / EUR_JPY;
    return `${SYM[currency]}${(eur * FX[currency]).toFixed(2)}`;
  };

  let grandTotal = 0;
  let totalTax   = 0;
  let totalComm  = 0;
  let totalFriend= 0;

  items.forEach(item => {
    const p = calcPrice(item.price_jpy, item.grade, country, currency);
    grandTotal  += p.total_jpy;
    totalTax    += p.tax_jpy;
    totalComm   += p.comm_jpy;
    totalFriend += p.friend_jpy;
  });

  return (
    <div style={{ fontSize: '13px' }}>
      {/* Líneas por producto */}
      {items.map((item, i) => {
        const p = calcPrice(item.price_jpy, item.grade, country, currency);
        return (
          <div key={i} style={{
            display: 'flex', justifyContent: 'space-between',
            padding: '6px 0', borderBottom: '1px solid #f0f0f0',
            gap: '8px',
          }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 500, color: '#111' }}>{item.card.name_en}</div>
              <div style={{ fontSize: '11px', color: '#aaa' }}>
                Estado {item.grade} · {item.source === 'pokecazilla' ? 'Pokécazilla' : 'SneakerDunk'}
              </div>
            </div>
            <div style={{ fontWeight: 500, whiteSpace: 'nowrap' }}>{p.display}</div>
          </div>
        );
      })}

      {/* Totales agrupados */}
      <div style={{ marginTop: '8px' }}>
        {[
          { label: 'Subtotal productos',              val: grandTotal - totalTax - totalComm - totalFriend },
          { label: 'Gestión local Japón (¥1.000/ud)', val: totalFriend },
          { label: 'Comisión de servicio (5%)',        val: totalComm },
          { label: `IVA importación ${COUNTRY_NAMES[country]}`, val: totalTax, highlight: true },
        ].map((row, i) => (
          <div key={i} style={{
            display: 'flex', justifyContent: 'space-between',
            padding: '5px 0', fontSize: row.highlight ? '12px' : '12px',
            color: row.highlight ? '#633806' : '#666',
          }}>
            <span>{row.label}</span>
            <span style={{ fontWeight: 500 }}>{fmt(row.val)}</span>
          </div>
        ))}

        <div style={{
          display: 'flex', justifyContent: 'space-between',
          padding: '10px 0 4px', fontSize: '16px', fontWeight: 700,
          borderTop: '1px solid #e8e8e8', marginTop: '4px',
        }}>
          <span>Total (sin envío)</span>
          <span style={{ color: '#e8001a' }}>{fmt(grandTotal)}</span>
        </div>

        <div style={{
          fontSize: '11px', color: '#aaa', padding: '8px',
          background: '#f9f9f9', borderRadius: '8px', marginTop: '6px',
          lineHeight: 1.5,
        }}>
          + Envío internacional calculado al finalizar según peso y destino.
          El IVA de importación ({Math.round((IMPORT_TAX[country] ?? 0.20) * 100)}%) está
          incluido en el total y será gestionado en tu nombre.
        </div>
      </div>
    </div>
  );
}

// ════════════════════════════════════════
// PÁGINA PRINCIPAL — pages/index.tsx
// ════════════════════════════════════════
// Importar los componentes anteriores y conectar con el backend:
//
// import { ..., CardListing, CartItem, Grade } from '../lib/types'
// import { CustomsBanner } from '../components/CustomsBanner'
// import { ProductCard }   from '../components/ProductCard'
// import { CardDetail }    from '../components/CardDetail'
// import { CheckoutSummary } from '../components/CheckoutSummary'
//
// const API_WS   = process.env.NEXT_PUBLIC_API_URL.replace(/^http/, 'ws')
// const API_HTTP = process.env.NEXT_PUBLIC_API_URL
//
// export default function Home() {
//   const [cards, setCards]       = useState<Record<number, CardListing>>({})
//   const [country, setCountry]   = useState('ES')
//   const [currency, setCurrency] = useState('EUR')
//   const [cart, setCart]         = useState<CartItem[]>([])
//   const [detail, setDetail]     = useState<CardListing | null>(null)
//   const wsRef = useRef<WebSocket>()
//
//   useEffect(() => {
//     const ws = new WebSocket(`${API_WS}/ws`)
//     wsRef.current = ws
//
//     ws.onmessage = (e) => {
//       const msg = JSON.parse(e.data)
//       if (msg.type === 'full_catalog') {
//         const map: Record<number, CardListing> = {}
//         msg.products.forEach((p: CardListing) => { map[p.id] = p })
//         setCards(map)
//       }
//       if (msg.type === 'price_update') {
//         setCards(prev => {
//           if (!prev[msg.card_id]) return prev
//           const updated = { ...prev[msg.card_id] }
//           updated.sources = updated.sources.map(s =>
//             s.source === msg.source ? { ...s, price_jpy: msg.new_jpy } : s
//           )
//           return { ...prev, [msg.card_id]: updated }
//         })
//       }
//     }
//     ws.onclose = () => setTimeout(() => connectWS(), 5000)
//     return () => ws.close()
//   }, [])
//
//   const addCart = (card: CardListing, grade: Grade, source: string) => {
//     const src = card.sources.find(s => s.source === source)
//     if (!src) return
//     const p = calcPrice(src.price_jpy, grade, country, currency)
//     setCart(c => [...c, { card, grade, source, price_jpy: src.price_jpy, price_final: p.total_final }])
//   }
//
//   return (
//     <>
//       <CustomsBanner country={country} onChangeCountry={setCountry} />
//       <div className="grid">
//         {Object.values(cards).map(c => (
//           <ProductCard key={c.id} card={c} country={country} currency={currency}
//             onOpen={setDetail} onAddCart={addCart} />
//         ))}
//       </div>
//       {detail && (
//         <CardDetail card={detail} country={country} currency={currency}
//           onClose={() => setDetail(null)} onAddCart={addCart} />
//       )}
//     </>
//   )
// }
