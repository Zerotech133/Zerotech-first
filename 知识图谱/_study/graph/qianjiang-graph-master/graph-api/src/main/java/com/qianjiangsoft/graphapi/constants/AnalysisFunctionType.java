package com.qianjiangsoft.graphapi.constants;

import lombok.AllArgsConstructor;
import lombok.ToString;

/**
 * @author 邓益聪
 * @date 2023/4/18 15:20
 */
@ToString
@AllArgsConstructor
public enum AnalysisFunctionType {

    /**
     * general : 查询
     * complex ：复杂查询
     * text ： 查找文本
     * statistics : 统计查询
     * findLoop : 查找回路
     * findPath : 探索里的查找路径
     * findSub : 探索里的连通性分析
     * flowDirection : 资金净流向分析
     * searchSameTagRelation 同类节点关系
     */

    GENERAL("general"),
    COMPLEX ("complex"),
    TEXT ("text"),
    STATISTICS ("statistics"),
    FIND_LOOP("findLoop"),
    FIND_PATH ("findPath"),
    FIND_SUB ("findSub"),
    FLAW_DIRECTION ("flowDirection"),

    SearchSameTagRelation("searchSameTagRelation");

    public final String type;
}
