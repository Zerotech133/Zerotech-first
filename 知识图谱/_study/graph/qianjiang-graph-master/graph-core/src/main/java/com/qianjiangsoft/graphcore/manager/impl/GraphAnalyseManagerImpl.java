package com.qianjiangsoft.graphcore.manager.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.map.MapUtil;
import com.qianjiangsoft.graphcore.constants.SortingDirection;
import com.qianjiangsoft.graphcore.constants.SortingType;
import com.qianjiangsoft.graphcore.dto.VertexSortDTO;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.helper.GraphAnalyseHelper;
import com.qianjiangsoft.graphcore.helper.ModelHelper;
import com.qianjiangsoft.graphcore.helper.StatisticsInfoHelper;
import com.qianjiangsoft.graphcore.manager.GraphAnalyseManager;
import com.qianjiangsoft.graphcore.model.*;
import com.qianjiangsoft.graphcore.param.Condition;
import com.qianjiangsoft.graphcore.param.FilterEdgeParam;
import com.qianjiangsoft.graphcore.param.FilterVertexParam;
import com.qianjiangsoft.graphcore.param.SortingParam;
import org.springframework.stereotype.Component;

import java.util.*;
import java.util.stream.Collectors;
import java.util.stream.Stream;

import static com.qianjiangsoft.graphcore.helper.GraphAnalyseHelper.*;

/**
 * @author marshallwu
 * @date 2022/6/16 11:42
 */
@Component
public class GraphAnalyseManagerImpl implements GraphAnalyseManager {

    @Override
    public List<MergeEdge> filterAndSortingEdgeListByParam(Set<Edge> edgeSet, Map<String, MergeEdgeStatisticsInfo> mergeEdgeStatisticsInfoMap, FilterEdgeParam filterEdgeParam, SortingParam sortingParam) {

        List<Edge> tempEdgeList = new ArrayList<>();

        //获取对应边类型
        for (Edge edge : edgeSet) {
            String key = StatisticsInfoHelper.getMergeEdgeKey(edge);
            MergeEdgeStatisticsInfo mergeEdgeStatisticsInfo = mergeEdgeStatisticsInfoMap.get(key);

            List<Boolean> checkResultList = new ArrayList<>();

            //没有统计数据,忽略数据
            if(mergeEdgeStatisticsInfo == null) {
                continue;
            }

            //强度
            Condition strengthCondition = filterEdgeParam.getStrengthCondition();
            if (BeanUtil.isNotEmpty(strengthCondition)){
                checkResultList.add(GraphAnalyseHelper.checkCondition(mergeEdgeStatisticsInfo.getStrength(), strengthCondition.getConditionType(),
                        strengthCondition.getValue(), strengthCondition.getValue2()));
            }

            //次数
            Condition countCondition = filterEdgeParam.getCountCondition();
            if (BeanUtil.isNotEmpty(countCondition)){
                checkResultList.add(GraphAnalyseHelper.checkCondition(mergeEdgeStatisticsInfo.getCount(), countCondition.getConditionType(),
                        countCondition.getValue(), countCondition.getValue2()));
            }

            //时间
            Condition timeCondition = filterEdgeParam.getTimeCondition();
            if (BeanUtil.isNotEmpty(timeCondition)){
                checkResultList.add(GraphAnalyseHelper.checkCondition(edge.getTime(), timeCondition.getConditionType(),
                        timeCondition.getValue(), timeCondition.getValue2()));
            }

            //属性
            Condition propertyCondition = filterEdgeParam.getPropertyCondition();
            if (BeanUtil.isNotEmpty(propertyCondition)){
                checkResultList.add(GraphAnalyseHelper.checkCondition(edge.getPropertyList().get(propertyCondition.getKey()), propertyCondition.getConditionType(),
                        propertyCondition.getValue(), propertyCondition.getValue2()));
            }

            //判断条件
            //计算是否符合条件
            boolean checkResult = true;
            for(Boolean value:checkResultList) {
                checkResult = checkResult && value;
            }
            if (checkResult) {
                tempEdgeList.add(edge);
            }
        }

        //转换成合并边
        List<MergeEdge> mergeEdgeList = ModelHelper.convertToMergeEdgeList(tempEdgeList, mergeEdgeStatisticsInfoMap);

        //排名
        if (sortingParam != null) {
            //对边进行排序
            return sortingEdgeList(mergeEdgeList, sortingParam);
        }

        return mergeEdgeList;
    }


    @Override
    public List<MergeEdge> filterAndSortingMergeEdgeListByParam(Set<MergeEdge> edgeSet, FilterEdgeParam filterEdgeParam, SortingParam sortingParam) {

        List<MergeEdge> tempEdgeList = new ArrayList<>();

        //获取对应边类型
        for (MergeEdge edge : edgeSet) {

            List<Boolean> checkResultList = new ArrayList<>();

            //强度
            Condition strengthCondition = filterEdgeParam.getStrengthCondition();
            if (BeanUtil.isNotEmpty(strengthCondition)){
                checkResultList.add(checkCondition(edge.getStrength(),strengthCondition.getConditionType(),
                        strengthCondition.getValue(), strengthCondition.getValue2()));
            }

            //次数
            Condition countCondition = filterEdgeParam.getCountCondition();
            if (BeanUtil.isNotEmpty(countCondition)){
                checkResultList.add(checkCondition(edge.getCount(), countCondition.getConditionType(),
                        countCondition.getValue(), countCondition.getValue2()));
            }


            //判断条件
            //计算是否符合条件
            boolean checkResult = true;
            for(Boolean value:checkResultList) {
                checkResult = checkResult && value;
            }
            if (checkResult) {
                tempEdgeList.add(edge);
            }
        }

        //排名
        if (sortingParam != null) {
            //对边进行排序
            return sortingEdgeList(tempEdgeList, sortingParam);
        }

        return tempEdgeList;
    }

    @Override
    public List<Vertex> filterVertexListByParam(List<Vertex> vertexList, FilterVertexParam filterVertexParam) {

        Map<String, VertexStatisticsInfo> vertexStatisticsInfoMap = SpaceContextHolder.get().getVertexStatisticsInfoMap();

        //参数
        List<Vertex> resultVertexList = new ArrayList<>();

        EdgeType edgeType = filterVertexParam.getEdgeType();

        for (Vertex vertex : vertexList) {

            //初始化
            List<Boolean> checkResultList = new ArrayList<>();

            //tag 和 name keyword判断
            checkResultList.add(checkVertexNameCondition(vertex, filterVertexParam.getTagName(), filterVertexParam.getKeyword()));

            if(edgeType != null) {

                VertexStatisticsInfo vertexStatisticsInfo = vertexStatisticsInfoMap
                        .get(StatisticsInfoHelper.getVertexStatisticsInfoKey(vertex.getId(), edgeType.getName()));

                if(vertexStatisticsInfo == null) {
                    continue;
                }

                //强度判断
                Condition strengthCondition = filterVertexParam.getStrengthCondition();
                if(BeanUtil.isNotEmpty(strengthCondition) && BeanUtil.isNotEmpty(strengthCondition.getConditionType())){
                    checkResultList.add(GraphAnalyseHelper.checkCondition(vertexStatisticsInfo.getAllStrength(), strengthCondition.getConditionType(),
                            strengthCondition.getValue(), strengthCondition.getValue2()));
                }

                //次数判断
                Condition countCondition = filterVertexParam.getCountCondition();
                if ((BeanUtil.isNotEmpty(countCondition))  && BeanUtil.isNotEmpty(countCondition.getConditionType())){
                    checkResultList.add(GraphAnalyseHelper.checkCondition(vertexStatisticsInfo.getAllCount(), countCondition.getConditionType(),
                            countCondition.getValue(), countCondition.getValue2()));
                }

                //出度判断
                Condition outDegreeCondition = filterVertexParam.getOutDegreeCondition();
                if ((BeanUtil.isNotEmpty(outDegreeCondition)) && BeanUtil.isNotEmpty(outDegreeCondition.getConditionType())){
                    checkResultList.add(GraphAnalyseHelper.checkCondition(vertexStatisticsInfo.getOutDegree(), outDegreeCondition.getConditionType(),
                            outDegreeCondition.getValue(), outDegreeCondition.getValue2()));
                }

                //入度判断
                Condition inDegreeCondition = filterVertexParam.getInDegreeCondition();
                if ((BeanUtil.isNotEmpty(inDegreeCondition)) && BeanUtil.isNotEmpty(inDegreeCondition.getConditionType())){
                    checkResultList.add(GraphAnalyseHelper.checkCondition(vertexStatisticsInfo.getInDegree(), inDegreeCondition.getConditionType(),
                            inDegreeCondition.getValue(), inDegreeCondition.getValue2()));
                }

                //总度数
                Condition allDegreeCondition = filterVertexParam.getAllDegreeCondition();
                if ((BeanUtil.isNotEmpty(allDegreeCondition)) && BeanUtil.isNotEmpty(allDegreeCondition.getConditionType())){
                    checkResultList.add(GraphAnalyseHelper.checkCondition(vertexStatisticsInfo.getAllDegree(), allDegreeCondition.getConditionType(),
                            allDegreeCondition.getValue(), allDegreeCondition.getValue2()));
                }
            }

            //属性过滤
            Condition propertyCondition = filterVertexParam.getPropertyCondition();
            if ((BeanUtil.isNotEmpty(propertyCondition))  && BeanUtil.isNotEmpty(propertyCondition.getConditionType())){
                checkResultList.add(GraphAnalyseHelper.checkCondition(vertex.getPropertyList().get(propertyCondition.getKey()),
                        propertyCondition.getConditionType(),
                        propertyCondition.getValue(), propertyCondition.getValue2()));
            }

            //计算是否符合条件
            boolean checkResult = true;
            for(Boolean value:checkResultList) {
                checkResult = checkResult && value;
            }
            if (checkResult) {
                resultVertexList.add(vertex);
            }
        }
        return resultVertexList;
    }


    /**
     * 对指定边列表按强度或次数排名
     * @param: mergeEdgeList
     * @param: space
     * @param: sortingParam
     * @return: java.util.List<com.qianjiangsoft.graphcore.model.Edge>
     * @author: marshall
     * @date: 2022/5/26
     */
    @Override
    public List<MergeEdge> sortingEdgeList(List<MergeEdge> mergeEdgeList, SortingParam sortingParam){

        //排序
        Stream<MergeEdge> mergeEdgeListStream = mergeEdgeList.stream();

        if(sortingParam.getType().equals(SortingType.strength)) {
            if (sortingParam.getDirection().equals(SortingDirection.asc)) {
                mergeEdgeListStream =
                        mergeEdgeListStream.sorted(Comparator.comparing(MergeEdge::getStrength));
            } else if (sortingParam.getDirection().equals(SortingDirection.desc)) {
                mergeEdgeListStream =
                        mergeEdgeListStream.sorted(Comparator.comparing(MergeEdge::getStrength).reversed());
            }
        }else if (sortingParam.getType().equals(SortingType.count)) {
            if (sortingParam.getDirection().equals(SortingDirection.asc)) {
                mergeEdgeListStream =
                        mergeEdgeListStream.sorted(Comparator.comparing(MergeEdge::getCount));
            } else if (sortingParam.getDirection().equals(SortingDirection.desc)) {
                mergeEdgeListStream =
                        mergeEdgeListStream.sorted(Comparator.comparing(MergeEdge::getCount).reversed());
            }
        }

        //返回结果
        return mergeEdgeListStream.limit(sortingParam.getLimit()).collect(Collectors.toList());
    }

    /**
     *  对实体进行强度次数排名
     * @param: vertexList
     * @param: space
     * @param: sortingParam
     * @param: edgeType
     * @return: java.util.List<com.qianjiangsoft.graphcore.model.Vertex>
     * @author: marshall
     * @date: 2022/5/26
     */
    @Override
    public List<Vertex> sortingVertexList(List<Vertex> vertexList, Map<String, VertexStatisticsInfo> vertexStatisticsInfoMap,
                                          SortingParam sortingParam, EdgeType edgeType){

        List<Vertex> result = new ArrayList<>();

        List<VertexSortDTO> vertexSortDTOList = new ArrayList<>();
        for (Vertex vertex : vertexList) {
            VertexSortDTO vertexSortDTO = new VertexSortDTO();
            vertexSortDTO.setVertex(vertex);

            //对统计值排序
            if(edgeType != null) {

                VertexStatisticsInfo vertexStatisticsInfo = vertexStatisticsInfoMap.get(StatisticsInfoHelper.getVertexStatisticsInfoKey(vertex.getId(), edgeType.getName()));

                //强度
                vertexSortDTO.setStrength(vertexStatisticsInfo.getAllStrength());

                //次数
                vertexSortDTO.setCount(vertexStatisticsInfo.getAllCount());

                //出度
                vertexSortDTO.setOutDegree(vertexStatisticsInfo.getOutDegree());

                //入度
                vertexSortDTO.setInDegree(vertexStatisticsInfo.getInDegree());

                //总度数
                vertexSortDTO.setAllDegree(vertexStatisticsInfo.getAllDegree());
            }

            //属性值
            vertexSortDTO.setPropertyValue(MapUtil.getDouble(vertex.getPropertyList(), sortingParam.getPropertyName(), 0D));

            vertexSortDTOList.add(vertexSortDTO);
        }

        //强度排名
        Stream<VertexSortDTO> sortListStream = vertexSortDTOList.stream();
        if (sortingParam.getType().equals(SortingType.strength)) {
            if (sortingParam.getDirection().equals(SortingDirection.asc)) {
                sortListStream = sortListStream.sorted(Comparator.comparing(VertexSortDTO::getStrength));
            } else if (sortingParam.getDirection().equals(SortingDirection.desc)) {
                sortListStream =
                        sortListStream.sorted(Comparator.comparing(VertexSortDTO::getStrength).reversed());
            }
        }

        //次数排名
        else if (sortingParam.getType().equals(SortingType.count)) {
            if (sortingParam.getDirection().equals(SortingDirection.asc)) {
                sortListStream = sortListStream.sorted(Comparator.comparing(VertexSortDTO::getCount));
            } else if (sortingParam.getDirection().equals(SortingDirection.desc)) {
                sortListStream =
                        sortListStream.sorted(Comparator.comparing(VertexSortDTO::getCount).reversed());
            }
        }else if (sortingParam.getType().equals(SortingType.outDegree)) {
            if (sortingParam.getDirection().equals(SortingDirection.asc)) {
                sortListStream = sortListStream.sorted(Comparator.comparing(VertexSortDTO::getOutDegree));
            } else if (sortingParam.getDirection().equals(SortingDirection.desc)) {
                sortListStream =
                        sortListStream.sorted(Comparator.comparing(VertexSortDTO::getOutDegree).reversed());
            }
        }else if (sortingParam.getType().equals(SortingType.inDegree)) {
            if (sortingParam.getDirection().equals(SortingDirection.asc)) {
                sortListStream = sortListStream.sorted(Comparator.comparing(VertexSortDTO::getInDegree));
            } else if (sortingParam.getDirection().equals(SortingDirection.desc)) {
                sortListStream =
                        sortListStream.sorted(Comparator.comparing(VertexSortDTO::getInDegree).reversed());
            }
        }else if (sortingParam.getType().equals(SortingType.allDegree)) {
            if (sortingParam.getDirection().equals(SortingDirection.asc)) {
                sortListStream = sortListStream.sorted(Comparator.comparing(VertexSortDTO::getAllDegree));
            } else if (sortingParam.getDirection().equals(SortingDirection.desc)) {
                sortListStream =
                        sortListStream.sorted(Comparator.comparing(VertexSortDTO::getAllDegree).reversed());
            }
        }else if (sortingParam.getType().equals(SortingType.property)) {
            if (sortingParam.getDirection().equals(SortingDirection.asc)) {
                sortListStream = sortListStream.sorted(Comparator.comparing(VertexSortDTO::getPropertyValue));
            } else if (sortingParam.getDirection().equals(SortingDirection.desc)) {
                sortListStream =
                        sortListStream.sorted(Comparator.comparing(VertexSortDTO::getPropertyValue).reversed());
            }
        }

        vertexSortDTOList = sortListStream.collect(Collectors.toList()).stream()
                .limit(sortingParam.getLimit()).collect(Collectors.toList());
        for (VertexSortDTO vertexSortDTO : vertexSortDTOList){
            result.add(vertexSortDTO.getVertex());
        }
        return result;
    }
}
