import { useState, useEffect } from "react";
import "./App.css";

const API_BASE_URL = "https://api.salamaxx97.online";
// 🔥 Cognito Config
const COGNITO_DOMAIN = process.env.REACT_APP_COGNITO_DOMAIN;
const CLIENT_ID = process.env.REACT_APP_CLIENT_ID;
const REDIRECT_URI =process.env.REACT_APP_REDIRECT_URI;;

function App() {
  const [products, setProducts] = useState([]);
  const [bestSellers, setBestSellers] = useState([]);
  const [cart, setCart] = useState([]);
  const [token, setToken] = useState(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [showCart, setShowCart] = useState(false);

  const [newProduct, setNewProduct] = useState({
    name: "",
    price: "",
    file: null,
  });

  const [uploading, setUploading] = useState(false);
  // State خاص بالمنتج اللي بنعدله حالياً
  const [editingProduct, setEditingProduct] = useState(null);
  // ================= JWT Decode =================
  const decodeJWT = (token) => {
    try {
      const base64Url = token.split(".")[1];
      const base64 = base64Url.replace(/-/g, "+").replace(/_/g, "/");
      const jsonPayload = decodeURIComponent(
        window
          .atob(base64)
          .split("")
          .map((c) => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2))
          .join("")
      );
      return JSON.parse(jsonPayload);
    } catch {
      return null;
    }
  };

// ================= LOGIN (COGNITO) =================
  const login = () => {
    const url =
      `${COGNITO_DOMAIN}/login?` +
      `client_id=${CLIENT_ID}` +
      `&response_type=token` +
      `&scope=openid+email+profile` +
      `&redirect_uri=${encodeURIComponent(REDIRECT_URI)}`;

    // 🔥 سطر كشف الحقيقة: هيطلعلك رسالة بالرابط اللي رايح لكوجنيتو
    alert("Sending Redirect URI: " + REDIRECT_URI); 

    window.location.href = url;
  };

  // ================= LOGOUT =================
  const logout = () => {
    localStorage.removeItem("token");

    window.location.href =
      `${COGNITO_DOMAIN}/logout?` +
      `client_id=${CLIENT_ID}` +
      `&logout_uri=${encodeURIComponent(REDIRECT_URI)}`;
  };

  // ================= HANDLE CALLBACK =================
  useEffect(() => {
    const hash = window.location.hash;

    if (hash.includes("id_token")) {
      const params = new URLSearchParams(hash.replace("#", "?"));
      const idToken = params.get("id_token");

      if (idToken) {
        localStorage.setItem("token", idToken);
        setToken(idToken);

        const decoded = decodeJWT(idToken);
        const groups = decoded?.["cognito:groups"] || [];

        setIsAdmin(groups.includes("Admins"));
      }

      // clean URL
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);

  // ================= RESTORE SESSION =================
  useEffect(() => {
    const saved = localStorage.getItem("token");

    if (saved) {
      setToken(saved);

      const decoded = decodeJWT(saved);
      const groups = decoded?.["cognito:groups"] || [];
      setIsAdmin(groups.includes("Admins"));
    }
  }, []);

  // ================= HEADERS =================
  const authHeaders = () => ({
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  });

  // ================= PRODUCTS =================
  const fetchProducts = async () => {
    const res = await fetch(`${API_BASE_URL}/products`);
    const data = await res.json();
    setProducts(data.data || []);
  };

  // ================= BEST SELLERS =================
  useEffect(() => {
    fetch(`${API_BASE_URL}/best-sellers`)
      .then((res) => res.json())
      .then((data) => setBestSellers(data.data || []));
  }, []);

  useEffect(() => {
    fetchProducts();
  }, []);

  // ================= UPLOAD PRODUCT =================
const handleAddProduct = async (e) => {
  e.preventDefault();
  setUploading(true);

  try {
    const res = await fetch(
      `${API_BASE_URL}/admin/products/upload-url`,
      {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          file_name: newProduct.file.name,
        }),
      }
    );

    const { upload_url, file_url } = await res.json();

    // ✅ FIX HERE
    await fetch(upload_url, {
      method: "PUT",
      body: newProduct.file,
      headers: {
        "Content-Type": newProduct.file.type,
      },
    });

    await fetch(`${API_BASE_URL}/admin/products`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({
        name: newProduct.name,
        price: Number(newProduct.price),
        image_url: file_url,
      }),
    });

    alert("Product added!");
  } catch (err) {
    console.error(err);
    alert("Upload failed");
  } finally {
    setUploading(false);
  }
};

// ================= DELETE PRODUCT =================
  const handleDeleteProduct = async (id) => {
    if (!window.confirm("هل أنت متأكد من حذف هذا المنتج؟")) return;

    try {
      const res = await fetch(`${API_BASE_URL}/admin/products/${id}`, {
        method: "DELETE",
        headers: authHeaders(),
      });

      if (res.ok) {
        alert("Product deleted!");
        fetchProducts(); // تحديث القائمة بعد الحذف
      } else {
        alert("Failed to delete product.");
      }
    } catch (err) {
      console.error(err);
      alert("Error deleting product.");
    }
  };

  // ================= UPDATE PRODUCT =================
  const handleUpdateProduct = async (e) => {
    e.preventDefault();
    setUploading(true);

    try {
      let finalImageUrl = editingProduct.image_url; // الاحتفاظ بالصورة القديمة كافتراضي

      // لو الأدمن اختار صورة جديدة، نرفعها الأول
      if (editingProduct.newFile) {
        const resUrl = await fetch(`${API_BASE_URL}/admin/products/upload-url`, {
          method: "POST",
          headers: authHeaders(),
          body: JSON.stringify({ file_name: editingProduct.newFile.name }),
        });

        const { upload_url, file_url } = await resUrl.json();

        await fetch(upload_url, {
          method: "PUT",
          body: editingProduct.newFile,
          headers: { "Content-Type": editingProduct.newFile.type },
        });

        finalImageUrl = file_url; // تحديث الرابط بالصورة الجديدة
      }

      // إرسال طلب التعديل للباك-اند
      const updateRes = await fetch(`${API_BASE_URL}/admin/products/${editingProduct.id}`, {
        method: "PUT",
        headers: authHeaders(),
        body: JSON.stringify({
          name: editingProduct.name,
          price: Number(editingProduct.price),
          image_url: finalImageUrl,
        }),
      });

      if (updateRes.ok) {
        alert("Product updated!");
        setEditingProduct(null); // قفل وضع التعديل
        fetchProducts(); // تحديث القائمة
      }
    } catch (err) {
      console.error(err);
      alert("Update failed");
    } finally {
      setUploading(false);
    }
  };
  // ================= CHECKOUT =================
  const checkout = async () => {
    const res = await fetch(`${API_BASE_URL}/checkout`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({
        items: cart,
        total: cart.reduce((a, b) => a + b.price, 0),
      }),
    });

    if (res.ok) {
      alert("Order placed!");
      setCart([]);
      setShowCart(false);
    }
  };

  // ================= UI =================
  return (
    <div style={{ fontFamily: "Arial", padding: 20 }}>

      {/* HEADER */}
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <h2>Cloud Store ☁️</h2>

        <div>
          {!token ? (
            <button onClick={login}>Login with Cognito</button>
          ) : (
            <button onClick={logout}>Logout</button>
          )}

          <button onClick={() => setShowCart(!showCart)}>
            Cart ({cart.length})
          </button>
        </div>
      </div>

      {/* CART */}
      {showCart && (
        <div>
          <h3>Cart</h3>
          {cart.map((i, idx) => (
            <p key={idx}>
              {i.name} - ${i.price}
            </p>
          ))}
          <button onClick={checkout}>Checkout</button>
        </div>
      )}

      {/* ADMIN PANEL & EDIT FORM */}
      {isAdmin && (
        <div style={{ background: "#f9f9f9", padding: 15, marginBottom: 20 }}>
          <h3>{editingProduct ? "Edit Product" : "Add New Product"}</h3>

          {/* فورم التعديل */}
          {editingProduct ? (
            <form onSubmit={handleUpdateProduct}>
              <input
                value={editingProduct.name}
                onChange={(e) => setEditingProduct({ ...editingProduct, name: e.target.value })}
                required
              />
              <input
                type="number"
                value={editingProduct.price}
                onChange={(e) => setEditingProduct({ ...editingProduct, price: e.target.value })}
                required
              />
              <input
                type="file"
                onChange={(e) => setEditingProduct({ ...editingProduct, newFile: e.target.files[0] })}
              />
              <button type="submit" disabled={uploading}>
                {uploading ? "Updating..." : "Save Changes"}
              </button>
              <button type="button" onClick={() => setEditingProduct(null)}>
                Cancel
              </button>
            </form>
          ) : (
            /* فورم الإضافة */
            <form onSubmit={handleAddProduct}>
              <input
                placeholder="Name"
                value={newProduct.name}
                onChange={(e) => setNewProduct({ ...newProduct, name: e.target.value })}
                required
              />
              <input
                type="number"
                placeholder="Price"
                value={newProduct.price}
                onChange={(e) => setNewProduct({ ...newProduct, price: e.target.value })}
                required
              />
              <input
                type="file"
                onChange={(e) => setNewProduct({ ...newProduct, file: e.target.files[0] })}
                required
              />
              <button type="submit" disabled={uploading}>
                {uploading ? "Uploading..." : "Add Product"}
              </button>
            </form>
          )}
        </div>
      )}

      {/* PRODUCTS (النسخة الوحيدة المدمجة) */}
      <h3>Products</h3>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {products.map((p) => (
          <div key={p.id} style={{ border: "1px solid #ddd", padding: 10, width: "200px" }}>
            <img src={p.image_url} width="100%" alt={p.name} style={{ height: "150px", objectFit: "cover" }} />
            <h4>{p.name}</h4>
            <p>${p.price}</p>
            <button onClick={() => setCart([...cart, p])}>Add to Cart</button>

            {/* أزرار الإدارة بتظهر للأدمن فقط */}
            {isAdmin && (
              <div style={{ marginTop: 10, display: "flex", gap: 5 }}>
                <button onClick={() => setEditingProduct(p)} style={{ background: "orange", color: "white", border: "none", padding: "5px 10px", cursor: "pointer" }}>
                  Edit
                </button>
                <button onClick={() => handleDeleteProduct(p.id)} style={{ background: "red", color: "white", border: "none", padding: "5px 10px", cursor: "pointer" }}>
                  Delete
                </button>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* BEST SELLERS */}
<h3 style={{ marginBottom: "12px" }}>🔥 Best Sellers</h3>

<div className="best-sellers-grid">
  {bestSellers.map((p) => (
    <div key={p.id} className="product-card">
      <img src={p.image_url} alt={p.name} />

      <div className="product-info">
        <h4>{p.name}</h4>
        <p>${p.price}</p>

        <span className="badge">🔥 Best Seller</span>
        </div>
        </div>
        ))}
    </div>
    </div>
  );
}



export default App;