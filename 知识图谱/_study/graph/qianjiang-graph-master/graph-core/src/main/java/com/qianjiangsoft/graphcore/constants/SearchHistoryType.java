package com.qianjiangsoft.graphcore.constants;

import lombok.AllArgsConstructor;
import lombok.ToString;

/**
 * @author ulmarr8861
 * @date 2022/9/5
 */
@ToString
@AllArgsConstructor
public enum SearchHistoryType {
    /**
     * general : 查询
     * complex ：复杂查询
     * text ： 查找文本
     * statistics : 统计查询
     * findLoop : 查找回路
     * findPath : 探索里的查找路径
     * findSub : 探索里的连通性分析
     * flowDirection : 资金净流向分析
     */
    general("general"),complex("complex"),text("text"),
    statistics("statistics"),findLoop("findLoop"),findPath("findPath"),
    findSub("findSub"), flowDirection("flowDirection");

    public final String name;

}
