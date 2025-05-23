from fastapi import APIRouter, Depends, HTTPException, status, Form, UploadFile, File, Query, Path
from sqlalchemy.orm import Session
from typing_extensions import Annotated
from sqlalchemy import func, cast, Float
from app.models import User, Product, ProductImage, Category,ProductVariant,VariantAttribute,CategoryVariantAttribute,Review
from app.schemas import  ProductCreate,ProductResponse, ProductVariantResponse, ProductVariantCreate
from app.database import get_db
from app.auth import get_current_user
from app.routers.admin import admin_required
from typing import Optional, List
from uuid import uuid4
import os, uuid, json, csv



router=APIRouter(prefix="/products", tags=["Product panel"])

UPLOAD_DIR = "media/uploads"
ERROR_DIR = "media/errors"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(ERROR_DIR, exist_ok=True)

# Add Products

@router.post("/", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def add_product(
    product_name: str = Form(...),
    brand: str = Form(...),
    is_feature: bool = Form(...),
    category_id: int = Form(...),
    description: str = Form(...),
    variants: List[str] = Form(...),
    variant_images: List[UploadFile] = File(...),
    admin: dict = Depends(admin_required),
    db: Session = Depends(get_db)
):
    if admin.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can add products")

    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=400, detail=f"Category ID {category_id} does not exist")

    def clean(val: str) -> str:
        return val.strip('"') if isinstance(val, str) else val

    product_name = clean(product_name)
    brand = clean(brand)
    description = clean(description)

    try:
        new_product = Product(
            sku=str(uuid.uuid4()),
            product_name=product_name,
            brand=brand,
            is_feature=is_feature,
            category_id=category_id,
            description=description,
            admin_id=admin.id
        )
        db.add(new_product)
        db.flush()
        db.refresh(new_product)

        image_index = 0
        created_variants = []

        for idx, variant_str in enumerate(variants):
            try:
                variant_data = json.loads(variant_str)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail=f"Variant at index {idx} has invalid JSON format.")

            required_fields = ["price", "stock"]
            for field in required_fields:
                if field not in variant_data:
                    raise HTTPException(status_code=400, detail=f"'{field}' is required in variant at index {idx}")

            attributes = variant_data.get("attributes", {})
            price = variant_data["price"]
            stock = variant_data["stock"]
            discount = variant_data.get("discount", 0)
            shipping_time = variant_data.get("shipping_time")
            image_count = variant_data.get("image_count", 1)

        # ----- Field validations -----
        if not isinstance(price, (int, float)) or price < 0:
            raise HTTPException(status_code=400, detail=f"Invalid 'price' in variant at index {idx}")

        if not isinstance(stock, int) or stock < 0:
            raise HTTPException(status_code=400, detail=f"Invalid 'stock' in variant at index {idx}")

        if not isinstance(discount, int) or not (0 <= discount <= 100):
            raise HTTPException(status_code=400, detail=f"'discount' must be between 0 and 100 at index {idx}")

        if shipping_time is not None and (not isinstance(shipping_time, int) or shipping_time < 0):
            raise HTTPException(status_code=400, detail=f"Invalid 'shipping_time' in variant at index {idx}")

        if not isinstance(attributes, dict):
            raise HTTPException(status_code=400, detail=f"'attributes' must be a dictionary in variant at index {idx}")
        
        if "color" not in attributes:
            raise HTTPException(status_code=400, detail=f"Missing 'color' attribute in variant at index {idx}")

        # ----- Check if color is a valid string (if it exists) -----
        if "color" in attributes and (not isinstance(attributes["color"], str) or not attributes["color"]):
            raise HTTPException(status_code=400, detail=f"Invalid 'color' attribute in variant at index {idx}")

        if not isinstance(image_count, int) or image_count < 1:
            raise HTTPException(status_code=400, detail=f"Invalid 'image_count' in variant at index {idx}")

            # Extract dynamic attributes
        direct_fields = {"price", "stock", "discount", "shipping_time", "image_count"}
        attributes = {k: v for k, v in variant_data.items() if k not in direct_fields}

        new_variant = ProductVariant(
                product_id=new_product.id,
                price=price,
                stock=stock,
                discount=discount,
                shipping_time=shipping_time,
                attributes=attributes
            )
        db.add(new_variant)
        db.flush()
        db.refresh(new_variant)    

        variant_image_urls = []
        if len(variant_images) < image_count:
            raise HTTPException(status_code=400, detail=f"Not enough images provided for variant at index {idx}. Expected {image_count} images, but received {len(variant_images)}.")

        if len(variant_images) > image_count:
            raise HTTPException(status_code=400, detail=f"Too many images provided for variant at index {idx}. Expected {image_count} images, but received {len(variant_images)}.")

        for _ in range(image_count):
            if image_index >= len(variant_images):
                raise HTTPException(status_code=400, detail=f"Not enough images provided for variant at index {idx}")

        for _ in range(image_count):
            if image_index >= len(variant_images):
                raise HTTPException(status_code=400, detail=f"Not enough images provided for variant at index {idx}")

            image = variant_images[image_index]
            short_id = uuid.uuid4().hex[:8]  
            clean_filename = image.filename.replace(" ", "_").lower()
            filename = f"{short_id}_{clean_filename}"
            file_path = os.path.join(UPLOAD_DIR, filename)

            with open(file_path, "wb") as buffer:
                buffer.write(await image.read())

                image_url = f"/media/uploads/{filename}"
                db.add(ProductImage(variant_id=new_variant.id, image_url=image_url))
                variant_image_urls.append(image_url)
                image_index += 1

        # ----- Build variant response -----
        created_variants.append({
            "id": new_variant.id,
            "price": price,
            "stock": stock,
            "discount": discount,
            "shipping_time": shipping_time,
            "attributes": attributes,
            "images": variant_image_urls
        })

        db.commit()

    except Exception as e:
        db.rollback()
        raise e

    return ProductResponse(
        id=new_product.id,
        sku=new_product.sku,
        product_name=new_product.product_name,
        brand=new_product.brand,
        category_id=new_product.category_id,
        description=new_product.description,
        admin_id=new_product.admin_id,
        created_at=new_product.created_at,
        updated_at=new_product.updated_at,
        variants=created_variants,
        images=[]
    )

# Create Products in Bulk Through CSV File

def save_image(file: UploadFile, product_name: str, attributes: dict) -> str:
    ext = os.path.splitext(file.filename)[1]
    product_slug = product_name.lower().replace(" ", "_")
    filename = f"{product_slug}_{uuid4().hex[:6]}{ext}"
    path = os.path.join(UPLOAD_DIR, filename)
    with open(path, "wb") as f:
        f.write(file.file.read())
    return f"/media/uploads/{filename}"


@router.post("/bulk-upload", status_code=status.HTTP_201_CREATED)
async def upload_products_csv(
    file: UploadFile = File(...),
    images: List[UploadFile] = File(...),
    admin=Depends(admin_required),
    db: Session = Depends(get_db)
):
    if admin.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can add products")

    content = await file.read()
    decoded = content.decode('utf-8').splitlines()
    reader = csv.DictReader(decoded)

    error_rows = []
    success_count = 0
    image_map = {img.filename: img for img in images}

    product_cache = {}

    for idx, row in enumerate(reader):
        try:
            # ----- Validate required fields -----
            required_fields = ["product_name", "brand", "is_feature", "category_id", "description",
                               "price", "stock", "attributes", "image_filenames"]
            for field in required_fields:
                if not row.get(field):
                    raise ValueError(f"Missing required field '{field}'")

            product_name = row["product_name"].strip()
            brand = row["brand"].strip()
            is_feature = row["is_feature"].lower() == "true"
            category_id = int(row["category_id"])
            description = row["description"].strip()
            price = float(row["price"])
            stock = int(row["stock"])
            discount = int(row.get("discount", 0))
            shipping_time = int(row.get("shipping_time", 0))
            attributes = json.loads(row["attributes"])
            image_filenames = [img.strip() for img in row["image_filenames"].split(",")]

            category = db.query(Category).filter(Category.id == category_id).first()
            if not category:
                raise ValueError(f"Category ID {category_id} does not exist")

            # ----- Check if product already created -----
            product_key = f"{product_name}_{category_id}"
            if product_key in product_cache:
                new_product = product_cache[product_key]
            else:
                new_product = Product(
                    sku=str(uuid4()),
                    product_name=product_name,
                    brand=brand,
                    is_feature=is_feature,
                    category_id=category_id,
                    description=description,
                    admin_id=admin.id
                )
                db.add(new_product)
                db.flush()
                db.refresh(new_product)
                product_cache[product_key] = new_product

            # ----- Create Variant -----
            new_variant = ProductVariant(
                product_id=new_product.id,
                price=price,
                stock=stock,
                discount=discount,
                shipping_time=shipping_time,
                attributes=attributes
            )
            db.add(new_variant)
            db.flush()
            db.refresh(new_variant)

            # ----- Save Images -----
            for img_name in image_filenames:
                if img_name not in image_map:
                    raise ValueError(f"Image file '{img_name}' not found in upload")
                img_url = save_image(image_map[img_name], product_name, attributes)
                db.add(ProductImage(variant_id=new_variant.id, image_url=img_url))

            db.commit()
            success_count += 1

        except Exception as e:
            db.rollback()
            row["error"] = str(e)
            error_rows.append(row)

    # ----- Save errors to CSV -----
    error_file_path = None
    if error_rows:
        error_file_path = os.path.join(ERROR_DIR, f"errors_{uuid4().hex[:6]}.csv")
        with open(error_file_path, "w", newline="", encoding="utf-8") as err_file:
            writer = csv.DictWriter(err_file, fieldnames=reader.fieldnames + ["error"])
            writer.writeheader()
            writer.writerows(error_rows)

    return {
        "message": f"{success_count} variants uploaded successfully",
        "errors": len(error_rows),
        "error_file": error_file_path,
        "error_details": error_rows[:5]
    }


# Get only featured products
@router.get("/featuredproducts", response_model=List[ProductResponse])
def get_featured_products(db: Session = Depends(get_db)):
    featured_products = db.query(Product).filter(Product.is_feature == True).all()

    product_responses = []
    for product in featured_products:
        variants = []
        for variant in product.variants:
            variant_dict = {
                "id": variant.id,
                "price": variant.price,
                "stock": variant.stock,
                "discount": variant.discount,
                "shipping_time": variant.shipping_time,
                "attributes": variant.attributes or {},
                "images": [img.image_url for img in variant.images],
            }
            variants.append(ProductVariantResponse(**variant_dict))

        product_dict = {
            "id": product.id,
            "sku": product.sku,
            "admin_id": product.admin_id,
            "product_name": product.product_name,
            "brand": product.brand,
            "category_id": product.category_id,
            "description": product.description,
            "created_at": product.created_at,
            "updated_at": product.updated_at,
            "variants": variants,
        }
        product_responses.append(ProductResponse(**product_dict))

    return product_responses


#get all products
@router.get("/allproducts", response_model=List[ProductResponse])
def get_products(db: Session = Depends(get_db)):
    products = db.query(Product).all()
    product_responses = []
    for product in products:
        variants = []
        for variant in product.variants:
            variant_dict = {
                "id": variant.id,
                "price": variant.price,
                "stock": variant.stock,
                "discount": variant.discount,
                "shipping_time": variant.shipping_time,
                "attributes": variant.attributes or {}, 
                "images": [img.image_url for img in variant.images],
            }
            variants.append(ProductVariantResponse(**variant_dict))

        product_dict = {
            "id": product.id,
            "sku": product.sku,
            "admin_id": product.admin_id,
            "product_name": product.product_name,
            "brand": product.brand,
            "category_id": product.category_id,
            "description": product.description,
            "created_at": product.created_at,
            "updated_at": product.updated_at,
            "variants": variants,
        }
        product_responses.append(ProductResponse(**product_dict))

    return product_responses

# GET product by ID

@router.get("/{product_id}", response_model=ProductResponse)
def get_product(product_id: Annotated[int, Path(ge=1)], db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")


    variants = db.query(ProductVariant).filter_by(product_id=product.id).all()
    variant_list = []
    for variant in variants:
        images = db.query(ProductImage).filter_by(variant_id=variant.id).all()
        image_urls = [img.image_url for img in images]
        variant_list.append({
            "id": variant.id,
            "price": variant.price,
            "stock": variant.stock,
            "discount": variant.discount,
            "shipping_time": variant.shipping_time,
            "attributes": variant.attributes,
            "images": image_urls
        })

    return ProductResponse(
        id=product.id,
        sku=product.sku,
        product_name=product.product_name,
        brand=product.brand,
        category_id=product.category_id,
        description=product.description,
        admin_id=product.admin_id,
        created_at=product.created_at,
        updated_at=product.updated_at,
        variants=variant_list,
        images=[]
    )

#get product by category
@router.get("/category/{category_id}", response_model=List[ProductResponse])
def get_products_by_category(category_id: Annotated[int, Path(ge=1)], db: Session = Depends(get_db)):
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(
            status_code=404,
            detail=f"Category with ID {category_id} does not exist."
        )
    products = db.query(Product).filter(Product.category_id == category_id).all()
    response = []
    for product in products:
        variants = db.query(ProductVariant).filter_by(product_id=product.id).all()
        variant_list = []
        for variant in variants:
            images = db.query(ProductImage).filter_by(variant_id=variant.id).all()
            image_urls = [img.image_url for img in images]
            variant_list.append({
                "id": variant.id,
                "price": variant.price,
                "stock": variant.stock,
                "discount": variant.discount,
                "shipping_time": variant.shipping_time,
                "attributes": variant.attributes,
                "images": image_urls
            })

        response.append(ProductResponse(
            id=product.id,
            sku=product.sku,
            product_name=product.product_name,
            brand=product.brand,
            category_id=product.category_id,
            description=product.description,
            admin_id=product.admin_id,
            created_at=product.created_at,
            updated_at=product.updated_at,
            variants=variant_list,
            images=[]
        ))

    return response


@router.delete("/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db), admin: dict = Depends(admin_required)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    # Step 1: Delete related reviews
    db.query(Review).filter_by(product_id=product_id).delete(synchronize_session=False)
    db.query(ProductImage).filter(ProductImage.variant_id.in_(
        db.query(ProductVariant.id).filter_by(product_id=product_id))).delete(synchronize_session=False)
    db.query(ProductVariant).filter_by(product_id=product_id).delete()
    db.delete(product)
    db.commit()
    return {"detail": "Product deleted successfully"}


#update the product
@router.put("/products/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: int,
    product_name: Optional[str] = Form(None),
    brand: Optional[str] = Form(None),
    category_id: Optional[int] = Form(None),
    description: Optional[str] = Form(None),
    variants: Optional[List[str]] = Form(None),
    variant_images: Optional[List[UploadFile]] = File(None),
    admin: dict = Depends(admin_required),
    db: Session = Depends(get_db)
):
    # Check if product exists
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Update basic product fields if provided
    if product_name:
        if not isinstance(product_name, str) or len(product_name.strip()) == 0:
            raise HTTPException(status_code=400, detail="Invalid product name")
        product.product_name = product_name.strip('"')

    if brand:
        if not isinstance(brand, str) or len(brand.strip()) == 0:
            raise HTTPException(status_code=400, detail="Invalid brand")
        product.brand = brand.strip('"')

    if description:
        if not isinstance(description, str) or len(description.strip()) == 0:
            raise HTTPException(status_code=400, detail="Invalid description")
        product.description = description.strip('"')

    if category_id:
        category = db.query(Category).filter(Category.id == category_id).first()
        if not category:
            raise HTTPException(status_code=400, detail="Category not found")
        product.category_id = category_id

    db.commit()
    db.refresh(product)

    created_variants = []
    image_index = 0

    if variants:
        # Remove old variants and images
        db.query(ProductImage).filter(ProductImage.variant_id.in_(
            db.query(ProductVariant.id).filter_by(product_id=product_id)
        )).delete(synchronize_session=False)

        db.query(ProductVariant).filter_by(product_id=product_id).delete()

        for idx, variant_str in enumerate(variants):
            try:
                variant_data = json.loads(variant_str)
            except json.JSONDecodeError:
                raise HTTPException(status_code=400, detail=f"Variant at index {idx} has invalid JSON format.")

            # Validate variant data using ProductVariantCreate model
            try:
                variant = ProductVariantCreate(**variant_data)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=f"Invalid data for variant at index {idx}: {e}")

            attributes = {k: v for k, v in variant_data.items() if k not in {"price", "stock", "discount", "shipping_time", "image_count"}}
            new_variant = ProductVariant(
                product_id=product.id,
                price=variant.price,
                stock=variant.stock,
                discount=variant.discount,
                shipping_time=variant.shipping_time,
                attributes=attributes
            )
            db.add(new_variant)
            db.commit()
            db.refresh(new_variant)

            image_count = variant.image_count
            image_urls = []
            for _ in range(image_count):
                if variant_images and image_index < len(variant_images):
                    image = variant_images[image_index]
                    filename = f"{uuid.uuid4()}_{image.filename}"
                    file_path = os.path.join(UPLOAD_DIR, filename)
                    with open(file_path, "wb") as buffer:
                        buffer.write(await image.read())
                    image_url = f"/static/uploads/{filename}"
                    db.add(ProductImage(variant_id=new_variant.id, image_url=image_url))
                    image_urls.append(image_url)
                    image_index += 1

            created_variants.append({
                "id": new_variant.id,
                "price": new_variant.price,
                "stock": new_variant.stock,
                "discount": new_variant.discount,
                "shipping_time": new_variant.shipping_time,
                "attributes": new_variant.attributes,
                "images": image_urls
            })

    return ProductResponse(
        id=product.id,
        sku=product.sku,
        product_name=product.product_name,
        brand=product.brand,
        category_id=product.category_id,
        description=product.description,
        admin_id=product.admin_id,
        created_at=product.created_at,
        updated_at=product.updated_at,
        variants= variants,
        images=[]
    )

#get product by category
@router.get("/category/{category_id}", response_model=List[ProductResponse])
def get_products_by_category(category_id: Annotated[int, Path(ge=1)], db: Session = Depends(get_db)):
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(
            status_code=404,
            detail=f"Category with ID {category_id} does not exist."
        )
    products = db.query(Product).filter(Product.category_id == category_id).all()
    response = []
    for product in products:
        variants = db.query(ProductVariant).filter_by(product_id=product.id).all()
        variant_list = []
        for variant in variants:
            images = db.query(ProductImage).filter_by(variant_id=variant.id).all()
            image_urls = [img.image_url for img in images]
            variant_list.append({
                "id": variant.id,
                "price": variant.price,
                "stock": variant.stock,
                "discount": variant.discount,
                "shipping_time": variant.shipping_time,
                "attributes": variant.attributes,
                "images": image_urls
            })

        response.append(ProductResponse(
            id=product.id,
            sku=product.sku,
            product_name=product.product_name,
            brand=product.brand,
            category_id=product.category_id,
            description=product.description,
            admin_id=product.admin_id,
            created_at=product.created_at,
            updated_at=product.updated_at,
            variants=variant_list,
            images=[]
        ))

    return response

# Filter Products by Rating

from app.models import Product, Review

@router.get("/rating/by-rating", response_model=List[ProductResponse])
def get_products_by_rating(
    min_rating: Optional[float] = Query(0, ge=0, le=5),
    db: Session = Depends(get_db)
):
    subquery = (
        db.query(
            Review.product_id,
            func.avg(Review.rating).label("avg_rating")
        )
        .group_by(Review.product_id)
        .subquery()
    )

    products = (
        db.query(Product)
        .join(subquery, Product.id == subquery.c.product_id)
        .filter(subquery.c.avg_rating >= min_rating)
        .all()
    )

    product_responses = []
    for product in products:
        variants = db.query(ProductVariant).filter_by(product_id=product.id).all()
        variant_list = []

        for variant in variants:
            images = db.query(ProductImage).filter_by(variant_id=variant.id).all()
            image_urls = [img.image_url for img in images]
            variant_list.append(ProductVariantResponse(
                id=variant.id,
                price=variant.price,
                stock=variant.stock,
                discount=variant.discount,
                shipping_time=variant.shipping_time,
                attributes=variant.attributes,
                images=image_urls
            ))

        product_responses.append(ProductResponse(
            id=product.id,
            sku=product.sku,
            product_name=product.product_name,
            brand=product.brand,
            category_id=product.category_id,
            description=product.description,
            admin_id=product.admin_id,
            created_at=product.created_at,
            updated_at=product.updated_at,
            variants=variant_list,
            images=[]
        ))

    return product_responses

 

# Delete products by ID

# @router.delete("/delete/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
# def delete_product(
#     product_id: int,
#     admin: dict = Depends(admin_required),
#     db: Session = Depends(get_db)
# ):
#     if admin.role != "admin":
#         raise HTTPException(status_code=403, detail="Only admins can delete products")

#     product = db.query(Product).filter(Product.id == product_id).first()
#     if not product:
#         raise HTTPException(status_code=404, detail="Product not found")

#     db.delete(product)
#     db.commit()

#     return {"detail": f"Product with ID {product_id} has been deleted successfully"}