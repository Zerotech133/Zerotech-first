package com.qianjiangsoft.graphcore.constants;


import java.util.Arrays;
import java.util.List;

/**
 * 基础库常量信息
 *
 * @author YoCiyy
 * @date 2021-04-07
 */
public interface BaseConstants {

    /**
     * 校验返回结果码
     */
    String GROUP_NAME_UNIQUE = "0";
    String GROUP_NAME_NOT_UNIQUE = "1";

    /**
     * 基础-分组类型（正常）
     */
    String TYPE_NORMAL = "N";

    /**
     * 基础-分组类型（原始库）
     */
    String TYPE_ORIGINAL = "O";

    /**
     * 基础-分组类型（过程库）
     */
    String TYPE_PROCESS = "P";

    /**
     * 基础-分组类型（沉淀库）
     */
    String TYPE_STAY = "S";

    String TYPE_RELATIONAL_GRAPH = "RG";

    String TYPE_FS = "FS";
    String TYPE_PA = "PA";

    /**
     * 案件状态：已结案
     */
    String CASE_CLOSE = "已结案";
    /**
     * 删除案件还有分组数据提示信息
     */
    String DELETE_CASE_GROUP = "案件下还有分组数据，不能删除案件信息！";
    /**
     * 删除标记：1
     */
    String DELETE_FLAG_ONE = "1";
    /**
     * 删除标记：0
     */
    String DELETE_FLAG_ZERO = "0";
    /**
     * 新增：add
     */
    String ADD = "add";
    /**
     * 修改：update
     */
    String UPDATE = "update";
    /**
     * 删除数据模型提示信息
     */
    String DELETE_TABLE_INFO = "该数据模型已投入使用，请勿进行删除操作！";
    /**
     * 主端
     */
    String PRIMARY = "主端";
    /**
     * 对端
     */
    String OPPOSITE = "对端";
    /**
     * 主端与对端出现的次数超标
     */
    String PRIMARY_OPPOSITE_NUM = "'主端'与'对端'的出现有且只能包含一次";
    /**
     * 主端与对端没有同时出现
     */
    String PRIMARY_OPPOSITE_INFO = "'主端'与'对端'必须同时出现";
    /**
     * 否
     */
    String NO = "否";
    /**
     * 原生
     */
    String PROTOGENESIS = "原生";
    /**
     * 文本
     */
    String FIELD_TYPE_CHARACTER = "文本";
    /**
     * 数字
     */
    String FIELD_TYPE_DIGITAL = "数值";
    /**
     * 日期
     */
    String FIELD_TYPE_DATE = "日期";
    /**
     * 排序
     */
    String SORT = "排序";

    /**
     * 空值
     */
    String EMPTY = "空值";
    /**
     * 非空
     */
    String NOT_EMPTY = "非空";
    /**
     * 介于
     */
    String BETWEEN = "介于";
    /**
     * 等于
     */
    String EQUALS = "等于";
    /**
     * 关键字匹配
     */
    String IN_KEYS = "关键字匹配";
    /**
     * 小
     */
    String SMALL = "小";
    /**
     * 早
     */
    String EARLY = "早";
    /**
     * 2
     */
    Integer TWO = 2;
    /**
     * 全选
     */
    String SELECT_ALL = "全选";
    /**
     * 关键字段
     */
    String EXCLUDE_FILED = "id,case_id,library_id,file_dir,file_name,line_number,sheet_index,sheet_name,create_time,table_id";

    String NULL_CASE_ID = "0";

    String NULL_PARENT_ID = "0";

    String CHINESE_YES = "是";
    String CHINESE_NO = "否";

    String DICT_TYPE_TABLE_TYPE = "base_table_info_type";

    String SPLIT_FIELD = "拆分";

    String ZIP = "zip";

    String RAR = "rar";

    String SEVENZ = "7z";

    String FS_ANXIN_HTML = "安信-HTML";

    String FS_MEIYA_HTML = "美亚-HTML";

    String FS_BEIJING_EFP = "北京标准-EFP";

    String FS_BCP = "BCP";


}
