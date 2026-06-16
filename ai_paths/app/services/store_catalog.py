from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StoreRecord:
    id: str
    name: str
    city: str
    address: str
    map_url: str = ""
    parking_name: str = ""
    parking_address: str = ""
    parking_link: str = ""
    business_hours: str = ""
    status_summary: str = "本地门店资料未包含暂停状态"
    is_public: bool = True


def local_store_records() -> list[StoreRecord]:
    return [
        StoreRecord(
            id="fallback-xiamen-jimei",
            name="厦门集美店",
            city="厦门",
            address="厦门市集美区",
            business_hours="10:00-19:00",
            status_summary="本地兜底资料，仅用于接口不可用时的方向参考",
        ),
        StoreRecord(
            id="385",
            name="厦门二店",
            city="厦门",
            address="厦门市湖里区湖里创新技术园嘉园大厦",
            map_url="https://mmapgwh.map.qq.com/shortlink/short?l=4c5285a342d450869ebc5ca83f86d3fe&tempSource=pcMap",
            parking_name="嘉园大厦停车场",
            parking_address="福建省厦门市湖里区安岭路与钟宅路交叉口附近",
            parking_link="https://mmapgwh.map.qq.com/shortlink/short?l=4a1d1e54db052935397ac7d621493b9b&tempSource=pcMap",
            business_hours="10:00-19:00",
        ),
        StoreRecord(
            id="fallback-shanghai-xuhui",
            name="上海徐汇店",
            city="上海",
            address="上海市徐汇区",
            business_hours="10:00-19:00",
            status_summary="本地兜底资料，仅用于接口不可用时的方向参考",
        ),
        StoreRecord(
            id="fallback-shanghai-jingan",
            name="上海静安店",
            city="上海",
            address="上海市静安区",
            business_hours="10:00-19:00",
            status_summary="本地兜底资料，仅用于接口不可用时的方向参考",
        ),
        StoreRecord(
            id="405",
            name="上海浦东二店",
            city="上海",
            address="上海市浦东新区杨高中路2108号FOR天物空间A栋",
            map_url="https://mmapgwh.map.qq.com/shortlink/short?l=14776fa967aad8a1aecf5059d4a415f4&tempSource=pcMap",
            parking_name="男龙总部园尧龙宫会中心停车场",
            parking_address="上海市浦东新区南洋泾路278号，芳甸路地铁站3号口步行140米",
            business_hours="10:00-19:00",
        ),
        StoreRecord(
            id="400",
            name="上海虹口店",
            city="上海",
            address="上海市虹口区花园路88-96号华博科技大厦",
            map_url="https://mmapgwh.map.qq.com/shortlink/short?l=f5334743dbe563dced62681169842a92&tempSource=pcMap",
            parking_name="华博科技大楼停车场",
            parking_address="上海市虹口区花园路88号华博科技大楼",
            business_hours="10:00-21:00",
        ),
        StoreRecord(
            id="322",
            name="上海嘉定店",
            city="上海",
            address="上海市嘉定区海波路766号点石矽金创意工坊",
            map_url="https://mmapgwh.map.qq.com/shortlink/short?l=a9dc89c22c641fb89df77afc09e9d049&tempSource=pcMap",
            parking_name="中星海兰苑停车场",
            parking_address="上海市嘉定区海波路中星海兰苑",
            business_hours="10:00-19:00",
        ),
        StoreRecord(
            id="fallback-shenzhen-luohu",
            name="深圳罗湖店",
            city="深圳",
            address="深圳市罗湖区",
            business_hours="10:00-19:00",
            status_summary="本地兜底资料，仅用于接口不可用时的方向参考",
        ),
        StoreRecord(
            id="fallback-shenzhen-futian",
            name="深圳福田店",
            city="深圳",
            address="深圳市福田区",
            business_hours="10:00-19:00",
            status_summary="本地兜底资料，仅用于接口不可用时的方向参考",
        ),
        StoreRecord(
            id="fallback-shenzhen-baoan",
            name="深圳宝安店",
            city="深圳",
            address="深圳市宝安区",
            business_hours="10:00-19:00",
            status_summary="本地兜底资料，仅用于接口不可用时的方向参考",
        ),
        StoreRecord(
            id="fallback-xian-xiaozhai",
            name="西安小寨店",
            city="西安",
            address="陕西省西安市雁塔区小寨西路232号置地时代MOMOPARK7号楼",
            parking_name="MOMOPARK艺术购物中心地下停车场",
            business_hours="10:00-19:00",
            status_summary="当前资料未显示暂停营业状态",
        ),
        StoreRecord(
            id="fallback-xian-weiyang",
            name="西安未央店",
            city="西安",
            address="西安市未央区凤城二路经发大厦A座",
            parking_name="西安经发大厦A座地下停车场",
            business_hours="10:00-19:00",
            status_summary="当前资料未显示暂停营业状态",
        ),
        StoreRecord(
            id="fallback-xian-beilin",
            name="西安碑林店",
            city="西安",
            address="西安市碑林区体育馆东路宏信国际花园7号楼",
            parking_name="香港宏信国际花园停车场",
            business_hours="10:00-19:00",
            status_summary="当前资料未显示暂停营业状态",
        ),
        StoreRecord(
            id="fallback-xian-gaoxin",
            name="西安高新店",
            city="西安",
            address="西安市高新区",
            business_hours="10:00-19:00",
            status_summary="本地兜底资料，仅用于接口不可用时的方向参考",
        ),
    ]
