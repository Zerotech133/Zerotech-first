package com.qianjiangsoft.graphapi.service.impl;

import cn.afterturn.easypoi.excel.annotation.Excel;
import cn.afterturn.easypoi.excel.entity.ExportParams;
import cn.afterturn.easypoi.excel.entity.params.ExcelExportEntity;
import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.collection.CollUtil;
import cn.hutool.core.comparator.CompareUtil;
import cn.hutool.core.convert.Convert;
import cn.hutool.core.date.DateUtil;
import cn.hutool.core.util.IdUtil;
import cn.hutool.core.util.ObjectUtil;
import cn.hutool.core.util.StrUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.qianjiangsoft.graphapi.dto.*;
import com.qianjiangsoft.graphapi.helper.GraphAnalyseHelper;
import com.qianjiangsoft.graphapi.param.*;
import com.qianjiangsoft.graphapi.service.AnalysisService;
import com.qianjiangsoft.graphcore.constants.*;
import com.qianjiangsoft.graphcore.domain.entity.SearchHistory;
import com.qianjiangsoft.graphcore.dto.SearchResultDTO;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.exception.BusinessException;
import com.qianjiangsoft.graphcore.mapper.SearchHistoryMapper;
import com.qianjiangsoft.graphcore.model.*;
import com.qianjiangsoft.graphcore.page.PageUtil;
import com.qianjiangsoft.graphcore.param.*;
import com.qianjiangsoft.graphcore.service.*;
import com.qianjiangsoft.graphcore.util.CommonServletUtil;
import com.qianjiangsoft.graphcore.util.EasyPoiUtil;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;

import javax.servlet.http.HttpServletResponse;
import java.lang.reflect.Field;
import java.text.SimpleDateFormat;
import java.util.*;
import java.util.stream.Collectors;

/**
 * @author ulmarr8861
 * @date 2022/4/18
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class AnalysisServiceImpl implements AnalysisService {

    private final GraphAnalyseService graphAnalysisService;

    private final SchemaService schemaService;

    private final GraphPathAnalyseService graphPathAnalyseService;

    private final GraphSearchService graphSearchService;

    private final VertexService vertexService;

    private final EdgeService edgeService;


    @Override
    public GraphCommonReturnDTO extendGraph(GraphExtendParam param) {
        Space space = SpaceContextHolder.get();
        List<EdgeType> edgeTypeList = new ArrayList<>();
        List<Vertex> pointList = new ArrayList<>();
        List<String> startVertexIdList = param.getStartVertexIdList();
        //算法返回值
        List<MergeEdge> mergeEdgeList;
        List<GraphCommonReturnDTO> graphExtendDTOList = new ArrayList<>();
        Set<GraphVertexReturnDTO> vertexList = new HashSet<>();
        Set<MergeEdgeDTO> edgeList = new HashSet<>();
        GraphCommonReturnDTO result = new GraphCommonReturnDTO();


        //获取edgeTypeList
        param.getEdgeTypeNameList().forEach(edgeTypeName -> {
            EdgeType edgeType = schemaService.findEdgeTypeByName(edgeTypeName, space.getId());
            if (edgeType != null) {
                edgeTypeList.add(edgeType);
            }
        });


        if (startVertexIdList != null && startVertexIdList.size() != 0) {
            startVertexIdList.forEach(vertexId -> pointList.add(space.getVertexMap().get(vertexId)));
        }

        //循环不同的点
        for (Vertex vertex : pointList) {
            ExtendGraphDataParam extendGraphDataParam = BeanUtil.copyProperties(param, ExtendGraphDataParam.class);
            extendGraphDataParam.setStartPoint(vertex);
            extendGraphDataParam.setEdgeTypeList(edgeTypeList);
            extendGraphDataParam.setDirection(param.getExtendDirection());
            mergeEdgeList = graphAnalysisService.extendGraphDataV2(extendGraphDataParam);
            graphExtendDTOList.add(GraphAnalyseHelper.convertMergeEdgeToGraphCommonReturnDTO(null, mergeEdgeList));
        }

        //对不同的结果列表，遍历，边加入到edgeList中，如果存在相同的边则不加入
        for (GraphCommonReturnDTO graphCommonReturn : graphExtendDTOList) {
            edgeList.addAll(graphCommonReturn.getEdgeList());

            //对所有的返回结果遍历，将点存入
            vertexList.addAll(graphCommonReturn.getVertexList());
        }

        //返回结果
        result.setVertexList(new ArrayList<>(vertexList));
        result.setEdgeList(new ArrayList<>(edgeList));
        return result;
    }


    @Override
    public GraphPathsReturnDTO graphFindPath(GraphExploreParam param) {
        Space space = SpaceContextHolder.get();
        Map<String, Vertex> vertexMap = space.getVertexMap();
        String spaceId = space.getId();
        List<EdgeType> edgeTypeList = new ArrayList<>();
        //传入参数
        Vertex source = vertexMap.get(param.getStartVertexId().getId());
        Vertex destination = vertexMap.get(param.getEndVertexId().getId());
        //算法返回值
        List<List<MergeEdge>> edgeMatrixList = new ArrayList<>();
        List<List<MergeEdge>> shortestPath=new ArrayList<>();


        if (source == null || destination == null) {
            throw new BusinessException(ResultCode.DATA_NOT_EXISTS, "内存数据不存在");
        }

        //获取EdgeTypeList(foreach适用于set集合，for适用于List集合)

        for (String edgeTypeName : param.getEdgeTypeNameList()) {
            edgeTypeList.add(schemaService.findEdgeTypeByName(edgeTypeName, spaceId));
        }

        //根据ConditionType分类
        if (SearchPathType.all.equals(param.getSearchPathType())) {
            SearchAllPathParam searchAllPathParam = new SearchAllPathParam(source, destination, edgeTypeList, param.getMaxStep().longValue(), param.getDirection(),(long)param.getTotal());
            edgeMatrixList = graphPathAnalyseService.searchAllPath(searchAllPathParam);
            if (edgeMatrixList.size() > param.getTotal()) {
                edgeMatrixList = edgeMatrixList.subList(0, param.getTotal());
            }
        } else if (SearchPathType.shortest.equals(param.getSearchPathType())) {
            SearchShortestPathParam searchShortestPathParam = new SearchShortestPathParam(source, destination, edgeTypeList, param.getMaxStep().longValue(), param.getDirection(),(long)param.getTotal());
            shortestPath = graphPathAnalyseService.searchShortestPath(searchShortestPathParam);
            edgeMatrixList.addAll(shortestPath);
        }

        return edgeMatrixDisintegration(edgeMatrixList);
    }

    /**
     * 将多条路径，提取vertexList、pathList。
     * vertexList:包含所有路径的vertex
     * pathList:List<List<MergeEdge>>   转化为   List<List<MergeEdgeDTO>>
     *
     * @param edgeMatrixList 算法返回的多条路径
     * @return 结果
     */
    public GraphPathsReturnDTO edgeMatrixDisintegration(List<List<MergeEdge>> edgeMatrixList) {
        Map<String, GraphVertexReturnDTO> vertexReturnDTOMap = new HashMap<>();
        List<List<MergeEdgeDTO>> pathList = new ArrayList<>();
        GraphPathsReturnDTO result = new GraphPathsReturnDTO();

        edgeMatrixList.remove(null);
        if (edgeMatrixList.isEmpty()) {
            result.setPathList(pathList);
            result.setVertexList(new ArrayList<>(vertexReturnDTOMap.values()));
            return result;
        }
        edgeMatrixList.forEach(edgeMatrix -> {
            List<MergeEdgeDTO> edgeList = new ArrayList<>();
            for (MergeEdge mergeEdge : edgeMatrix) {
                //Edge取出的Vertex点
                //逻辑：把每个edge的source和destination点取出放入
                GraphVertexReturnDTO vertexSourceDTO = GraphAnalyseHelper.convertVertexToGraphVertexDTO(mergeEdge.getSource());
                vertexReturnDTOMap.put(vertexSourceDTO.getId(), vertexSourceDTO);

                GraphVertexReturnDTO vertexDeDTO = GraphAnalyseHelper.convertVertexToGraphVertexDTO(mergeEdge.getDestination());
                vertexReturnDTOMap.put(vertexDeDTO.getId(), vertexDeDTO);


                //取边
                MergeEdgeDTO mergeEdgeDTO = BeanUtil.copyProperties(mergeEdge, MergeEdgeDTO.class);
                mergeEdgeDTO.setSourceId(mergeEdge.getSource().getId());
                mergeEdgeDTO.setDestinationId(mergeEdge.getDestination().getId());
                mergeEdgeDTO.setEdgeTypeName(mergeEdge.getType().getName());
                edgeList.add(mergeEdgeDTO);

            }
            //将edge取出
            pathList.add(edgeList);
        });

        result.setVertexList(new ArrayList<>(vertexReturnDTOMap.values()));
        result.setPathList(pathList);

        return result;
    }

    @Override
    public GraphCommonReturnDTO searchSubgraph(SearchSubGraphParam param) {
        List<Vertex> startPointList = new ArrayList<>();
        Space space = SpaceContextHolder.get();
        List<String> vertexIdList = param.getVertexIdList();
        Map<String, Vertex> vertexMap = space.getVertexMap();
        SearchSubgraphParam searchSubgraphParam = new SearchSubgraphParam();
        List<SearchSubgraphEdgeParam> searchSubgraphParamList = new ArrayList<>();
        SearchSubgraphEdgeParam searchSubgraphEdgeParam;


        //获得参数
        if (vertexIdList == null || vertexIdList.size() == 0) {
            return new GraphCommonReturnDTO();
        }
        vertexIdList.forEach(vertexId -> startPointList.add(vertexMap.get(vertexId)));

        if (param.getEdgeParamList() == null || param.getEdgeParamList().size() == 0) {
            searchSubgraphParamList.add(null);
        } else {
            for (FindSubGraphParam edgeParam : param.getEdgeParamList()) {
                if (StrUtil.isEmpty(edgeParam.getEdgeTypeName())) {
                    searchSubgraphParamList.add(null);

                } else {
                    searchSubgraphEdgeParam = BeanUtil.copyProperties(edgeParam, SearchSubgraphEdgeParam.class);
                    searchSubgraphEdgeParam.setEdgeType(schemaService.findEdgeTypeByName(edgeParam.getEdgeTypeName(), space.getId()));
                    searchSubgraphParamList.add(searchSubgraphEdgeParam);
                }
            }
        }

        searchSubgraphParam.setStartPointList(startPointList);
        searchSubgraphParam.setMaxStep(param.getStep());
        searchSubgraphParam.setEdgeParamList(searchSubgraphParamList);

        //参数塞入算法
        long a = System.currentTimeMillis();
        List<MergeEdge> mergeEdgeList = graphAnalysisService.searchSubgraph(searchSubgraphParam);
        long b = System.currentTimeMillis();
        System.out.println(b - a + "算法");

        //返回结果加入起始点中的孤立点
        Map<String, Vertex> startPointMap = startPointList.stream().collect(Collectors.toMap(Vertex::getId, o -> o));

        //返回结果
        return GraphAnalyseHelper.convertMergeEdgeToGraphCommonReturnDTO(startPointMap, mergeEdgeList);
    }


    @Override
    public GraphCommonReturnDTO complexSearch(GraphComplexSearch param) {
        //参数
        Space space = SpaceContextHolder.get();

        ComplexSearchParam complexSearchParam = BeanUtil.copyProperties(param, ComplexSearchParam.class);

        Tag tag = schemaService.findTagByName(param.getVertexParam().getTagName(), space.getId());
        complexSearchParam.getVertexParam().setTag(tag);

        if (BeanUtil.isNotEmpty(param.getVertexParam().getStatisticsCondition())) {
            EdgeType edgeType = schemaService.findEdgeTypeByName(param.getVertexParam().getStatisticsCondition().getEdgeTypeName(), space.getId());
            complexSearchParam.getVertexParam().getStatisticsCondition().setEdgeType(edgeType);
        }

        if (SearchMode.all.equals(param.getMode())) {
            Tag tag2 = schemaService.findTagByName(param.getVertexParam2().getTagName(), space.getId());
            complexSearchParam.getVertexParam2().setTag(tag2);

            if (BeanUtil.isNotEmpty(param.getVertexParam2().getStatisticsCondition())) {
                EdgeType edgeType = schemaService.findEdgeTypeByName(param.getVertexParam2().getStatisticsCondition().getEdgeTypeName(), space.getId());
                complexSearchParam.getVertexParam2().getStatisticsCondition().setEdgeType(edgeType);
            }

            EdgeType et = schemaService.findEdgeTypeByName(param.getEdgeParam().getEdgeTypeName(), space.getId());
            complexSearchParam.getEdgeParam().setEdgeType(et);

        }

        SearchResultDTO searchResultDTO = graphSearchService.complexSearch(complexSearchParam);

        if (ObjectUtil.isEmpty(searchResultDTO)) {
            return null;
        }
        //放回结果
        return GraphAnalyseHelper.convertMergeEdgeToGraphCommonReturnDTO(searchResultDTO.getVertexMap(), searchResultDTO.getMergeEdgeList());
    }

    @Override
    public GraphPathsReturnDTO graphFindLoop(GraphFindLoopParam param) {
        Space space = SpaceContextHolder.get();
        List<Vertex> pointList = new ArrayList<>();
        List<EdgeType> edgeTypeList = new ArrayList<>();


        //参数
        //获得pointList参数
        if (param.getVertexIdList() != null) {
            param.getVertexIdList().forEach(vertexId -> pointList.add(space.getVertexMap().get(vertexId)));
        }
        //获取edgeTypeList
        param.getEdgeTypeNameList().forEach(edgeTypeName -> {
            EdgeType edgeType = schemaService.findEdgeTypeByName(edgeTypeName, space.getId());
            if (BeanUtil.isNotEmpty(edgeType)) {
                edgeTypeList.add(edgeType);
            }
        });

        SearchLoopPathParam searchLoopPathParam = BeanUtil.copyProperties(param, SearchLoopPathParam.class);
        searchLoopPathParam.setPointList(pointList);
        searchLoopPathParam.setTypeList(edgeTypeList);

        List<List<MergeEdge>> searchLoopPathReturn = graphPathAnalyseService.searchLoopPath(searchLoopPathParam);

        return edgeMatrixDisintegration(searchLoopPathReturn);

    }


    @Override
    public GraphCommonReturnDTO textSearch(TextSearchParam param) {
        SearchResultDTO searchResultDTO = graphSearchService.textSearch(param);
        return GraphAnalyseHelper.convertMergeEdgeToGraphCommonReturnDTO(searchResultDTO.getVertexMap(), searchResultDTO.getMergeEdgeList());
    }

    @Override
    public GraphCommonReturnDTO graphStatistics(GraphStatisticsParam param) {
        StatisticsSearchParam statisticsSearchParam = BeanUtil.copyProperties(param, StatisticsSearchParam.class);
        EdgeType edgeType = schemaService.findEdgeTypeByName(param.getEdgeTypeName(), SpaceContextHolder.get().getId());
        statisticsSearchParam.setEdgeType(edgeType);
        if (StrUtil.containsAny(param.getStrengthCondition().getConditionType(), ConditionType.MAX, ConditionType.MIN)) {
            statisticsSearchParam.setStrengthCondition(null);
        } else if (StrUtil.containsAny(param.getCountCondition().getConditionType(), ConditionType.MAX, ConditionType.MIN)) {
            statisticsSearchParam.setCountCondition(null);
        }

        SearchResultDTO searchResultDTO = graphSearchService.statisticsSearch(statisticsSearchParam);

        return GraphAnalyseHelper.convertMergeEdgeToGraphCommonReturnDTO(searchResultDTO.getVertexMap(), searchResultDTO.getMergeEdgeList());


    }

    @Override
    public List<VertexReturnDTO> searchVertexListByName(searchVertexListByNameParam param) {
        List<Vertex> vertexList = vertexService.searchVertexListByName(param.getKeyword(), param.getTagName()).
                stream().limit(50L).collect(Collectors.toList());
        Comparator<VertexReturnDTO> comparator = CompareUtil.comparingPinyin(VertexReturnDTO::getName);
        return CollUtil.sort(BeanUtil.copyToList(vertexList, VertexReturnDTO.class), comparator);
    }

    @Override
    public List<EdgeReturnDTO> queryEdgeByMergeEdge(MergeEdgeConvertParam param) {
        Space space = SpaceContextHolder.get();
        Map<String, Vertex> vertexMap = space.getVertexMap();
        //参数
        Vertex source = vertexMap.get(param.getSourceId());
        Vertex destination = vertexMap.get(param.getDestinationId());
        MergeEdgeParam mergeEdgeParam = new MergeEdgeParam(source, destination,
                schemaService.findEdgeTypeByName(param.getEdgeTypeName(), space.getId()));
        List<Edge> result = edgeService.queryEdgeListByMergeEdge(mergeEdgeParam);

        //返回值包装
        return result.stream().map(edge -> {
            EdgeReturnDTO edgeReturnDTO = BeanUtil.copyProperties(edge, EdgeReturnDTO.class);
            edgeReturnDTO.setSource(edge.getSource().getName());
            edgeReturnDTO.setDestination(edge.getDestination().getName());
            edgeReturnDTO.setType(edge.getType().getName());
            return edgeReturnDTO;
        }).collect(Collectors.toList());
    }


    @Override
    public GraphCommonReturnDTO analyseFlowDirection(FlowDirectionParam flowDirectionParam) {
        log.info("资金流向传递参数：" + flowDirectionParam.toString());
        AnalyseFlowDirectionParam analyseFlowDirectionParam = new AnalyseFlowDirectionParam();
        Map<String, Vertex> vertexMap = new HashMap<>();

        EdgeType edgeType = schemaService.findEdgeTypeByName(flowDirectionParam.getEdgeTypeName(), SpaceContextHolder.get().getId());
        analyseFlowDirectionParam.setEdgeType(edgeType);
        if (StrUtil.isNotEmpty(flowDirectionParam.getStrengthCondition().getConditionType())
                && StrUtil.isNotEmpty(flowDirectionParam.getStrengthCondition().getValue())) {
            analyseFlowDirectionParam.setStrengthCondition(flowDirectionParam.getStrengthCondition());
        }
        List<MergeEdge> mergeEdgeList = graphAnalysisService.analyseFlowDirection(analyseFlowDirectionParam);

        //返回值处理
        for (MergeEdge mergeEdge : mergeEdgeList) {
            vertexMap.put(mergeEdge.getSource().getId(), mergeEdge.getSource());
            vertexMap.put(mergeEdge.getDestination().getId(), mergeEdge.getDestination());
        }

        return GraphAnalyseHelper.convertMergeEdgeToGraphCommonReturnDTO(vertexMap, mergeEdgeList);
    }

    @Override
    public void exportExcel(MergeEdgeConvertParam param) {
        HttpServletResponse response = CommonServletUtil.getResponse();
        //1、组装EasyPoiUtil.exportAndDownload参数
        //1、1导出文件名
        Date date = new Date();
        SimpleDateFormat simpleDateFormat = new SimpleDateFormat("yyyyMMdd");
        String fileName = "原始数据" + simpleDateFormat.format(date) + ".xls";

        //1、2导出参数（标题、sheet名称、是否创建表头，表格类型）
        ExportParams exportParams = new ExportParams();
        exportParams.setTitle(Constants.ExportExcelConfig.EXCEL_TITLE);
        exportParams.setSheetName(fileName);
        List<EdgeReturnDTO> edgeReturnDTOList = queryEdgeByMergeEdge(param);

        //1、3数据列表
        Field[] fields = EdgeReturnDTO.class.getDeclaredFields();
        Map<String, Object> propertyCollumMap = edgeReturnDTOList.get(0).getPropertyList();
        List<Map<String, Object>> dataList = new ArrayList<>();
        for (EdgeReturnDTO edgeReturnDTO : edgeReturnDTOList) {
            try {
                //初始化hashMap大小
                Map<String, Object> mapData = new HashMap<>(dataList.size());
                objectToMap(edgeReturnDTO, fields[fields.length - 1], mapData);
                //properties数据添加到mapData里
                mapData.putAll(edgeReturnDTO.getPropertyList());
                dataList.add(mapData);
            } catch (IllegalAccessException e) {
                e.printStackTrace();
            }
        }
        //1、4表头
        List<ExcelExportEntity> collumList = new ArrayList<>();
        //表头EdgeReturnDTO中(去除propertyList)
        for (int i = 0; i < fields.length - 1; i++) {
            ExcelExportEntity colEntity = new ExcelExportEntity(fields[i].getAnnotation(Excel.class).name(), fields[i].getName());
            collumList.add(colEntity);
        }
        //处理propertyList
        propertyCollumMap.forEach((key, value) -> {
            ExcelExportEntity colEntity = new ExcelExportEntity(key, key);
            collumList.add(colEntity);
        });

        EasyPoiUtil.exportAndDownload(dataList, collumList, fileName, response, exportParams);
    }

    /**
     * 将对象装换成map
     *
     * @param obj         将对象装换成map
     * @param ignoreField ignoreField
     * @param targetMap   返回Map
     * @throws IllegalAccessException 异常
     */
    public static void objectToMap(Object obj, Field ignoreField, Map<String, Object> targetMap) throws IllegalAccessException {
        Class<?> clazz = obj.getClass();
        for (Field field : clazz.getDeclaredFields()) {
            if (field.equals(ignoreField)) {
                continue;
            }
            field.setAccessible(true);
            String fieldName = field.getName();
            Object value = field.get(obj);
            targetMap.put(fieldName, value);
        }
    }



    @Override
    public GraphCommonReturnDTO searchSameTagRelation(SearchSameTagRelationParam param) {
        Space space = SpaceContextHolder.get();

        Tag tag = schemaService.findTagByName(param.getTagName(), space.getId());
        SearchSameTagPathParam sameTagPathParam = new SearchSameTagPathParam();
        sameTagPathParam.setTag(tag);
        sameTagPathParam.setMaxStep(param.getMaxStep());
        if(CollUtil.isNotEmpty(param.getVertexIdList())) {
            sameTagPathParam.setVertexList(param.getVertexIdList().stream()
                    .map(vertexService::findVertexById).collect(Collectors.toList()));
        }

        List<MergeEdge> mergeEdgeList = graphPathAnalyseService.searchSameTagPath(sameTagPathParam);

        return GraphAnalyseHelper.convertMergeEdgeToGraphCommonReturnDTO(null, mergeEdgeList);
    }
}
